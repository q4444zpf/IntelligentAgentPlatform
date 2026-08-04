import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session, sessionmaker

from app.agents.router import create_router
from app.agents.schemas import AgentConfig, AgentCreateRequest, AgentDefaultRequest
from app.agents.service import BUILTIN_AGENT_ID, AgentService
from app.agents.store import AgentConcurrentUpdateError, DEFAULT_SETTING_KEY, AgentStore
from app.db.base import Base
from app.db.platform_models import PlatformSettingRecord, RegisteredToolRecord
from app.skills.schemas import SkillCreateRequest
from app.skills.service import SkillService

AUTH_HEADERS = {
    "X-Unit-ID": "unit-1",
    "X-User-ID": "u1",
    "X-Project-ID": "p1",
    "X-User-Role": "admin",
}


class CountingAgentStore(AgentStore):
    def __init__(self, session_factory):
        super().__init__(session_factory)
        self.default_pointer_reads = 0

    def get_default_id(self):
        self.default_pointer_reads += 1
        return super().get_default_id()


class StaleBuiltinSnapshotStore(AgentStore):
    def __init__(self, session_factory, stale_record):
        super().__init__(session_factory)
        self.stale_record = stale_record
        self.return_stale_once = True

    def get(self, agent_id):
        if agent_id == BUILTIN_AGENT_ID and self.return_stale_once:
            self.return_stale_once = False
            return dict(self.stale_record)
        return super().get(agent_id)


class AtomicOperationSpyStore(AgentStore):
    def __init__(self, session_factory):
        super().__init__(session_factory)
        self.atomic_calls = []
        self.reject_legacy_mutations = False

    def set_default_id(self, agent_id, expected_version):
        if self.reject_legacy_mutations:
            raise AssertionError("service used non-atomic set_default_id")
        return super().set_default_id(agent_id, expected_version)

    def update(self, agent_id, config):
        if self.reject_legacy_mutations:
            raise AssertionError("service used non-atomic update")
        return super().update(agent_id, config)

    def delete(self, agent_id):
        if self.reject_legacy_mutations:
            raise AssertionError("service used non-atomic delete")
        return super().delete(agent_id)

    def set_default_agent(self, agent_id, expected_version):
        self.atomic_calls.append(("set_default_agent", agent_id))
        self.reject_legacy_mutations = False
        try:
            super().set_default_id(agent_id, expected_version)
            return super().get(agent_id)
        finally:
            self.reject_legacy_mutations = True

    def update_agent(self, agent_id, config):
        self.atomic_calls.append(("update_agent", agent_id))
        self.reject_legacy_mutations = False
        try:
            return super().update(agent_id, config)
        finally:
            self.reject_legacy_mutations = True

    def set_enabled_agent(self, agent_id, enabled):
        self.atomic_calls.append(("set_enabled_agent", agent_id))
        current = super().get(agent_id)
        config = {name: current[name] for name in AgentConfig.model_fields}
        config["enabled"] = enabled
        self.reject_legacy_mutations = False
        try:
            return super().update(agent_id, config)
        finally:
            self.reject_legacy_mutations = True

    def delete_agent(self, agent_id, builtin_agent_id):
        self.atomic_calls.append(("delete_agent", agent_id))
        self.reject_legacy_mutations = False
        try:
            return super().delete(agent_id)
        finally:
            self.reject_legacy_mutations = True


@pytest.fixture
def client(tmp_path):
    skill_service = SkillService(tmp_path / "skills")
    skill_service.create(
        SkillCreateRequest(
            name="flood-forecast",
            description="洪水预报",
            content="""---
name: flood-forecast
description: 洪水预报
version: "1.0"
---
# 洪水预报
""",
            tags=["水文"],
        )
    )
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(engine)
    store = AgentStore(
        sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    )
    service = AgentService(
        store, skill_service=skill_service, workspace_root=tmp_path / "agent-workspaces"
    )
    app = FastAPI()
    app.state.allow_dev_identity = True
    app.include_router(create_router(service), prefix="/api/agents")
    app.state.agent_service = service
    return TestClient(app, headers=AUTH_HEADERS)


def agent_payload(**overrides):
    payload = {
        "id": "reservoir-dispatch",
        "name": "水库调度智能体",
        "description": "分析来水并生成调度建议",
        "runtime_form": "desktop",
        "language": "zh-CN",
        "provider_id": "water-model",
        "model": "water-chat",
        "system_prompt": "你是水库调度专家。",
        "context_prompt": "结合本地工程文件和客户端状态回答。",
        "approval_policy": "control_commands",
        "skill_names": ["flood-forecast"],
        "tool_ids": [],
        "enabled": True,
    }
    payload.update(overrides)
    return payload


def test_unit_auditor_cannot_create_agent(client):
    response = client.post(
        "/api/agents",
        json=agent_payload(),
        headers={"X-User-Roles": "unit_auditor"},
    )
    assert response.status_code == 403


BUILTIN_TOOL_IDS = [
    "system.get_current_time",
    "system.get_runtime_context",
]


def test_normalizes_agent_tool_ids_with_stable_deduplication():
    config = AgentConfig(
        name="工具智能体",
        tool_ids=[" system.get_current_time ", "", "system.get_current_time"],
    )

    assert config.tool_ids == ["system.get_current_time"]


def test_builtin_default_agent_binds_required_tools(client):
    response = client.get(f"/api/agents/{BUILTIN_AGENT_ID}")

    assert response.status_code == 200
    assert response.json()["tool_ids"] == BUILTIN_TOOL_IDS


@pytest.mark.parametrize("operation", ["create", "update"])
def test_rejects_unknown_tool_bindings(client, operation):
    payload = agent_payload(tool_ids=["missing.tool"])
    if operation == "create":
        response = client.post("/api/agents", json=payload)
    else:
        assert client.post("/api/agents", json=agent_payload()).status_code == 201
        response = client.put(
            "/api/agents/reservoir-dispatch",
            json={key: value for key, value in payload.items() if key != "id"},
        )

    assert response.status_code == 422
    assert "missing.tool" in response.json()["detail"]


@pytest.mark.parametrize("published,enabled", [(True, False), (False, True)])
def test_rejects_disabled_or_unpublished_tool_bindings(client, published, enabled):
    service = client.app.state.agent_service
    tool_id = "custom.test_tool"
    with service.tool_service.store.session_factory.begin() as session:
        session.add(
            RegisteredToolRecord(
                tool_id=tool_id,
                version="1.0.0",
                name="测试工具",
                description="仅用于绑定校验",
                source="mcp",
                risk_level="low",
                input_schema={},
                output_schema={},
                requires_approval=False,
                published=published,
                enabled=enabled,
            )
        )

    response = client.post(
        "/api/agents",
        json=agent_payload(tool_ids=[tool_id]),
    )

    assert response.status_code == 422
    assert tool_id in response.json()["detail"]


def test_updates_and_copies_agent_tool_bindings(client):
    assert client.post("/api/agents", json=agent_payload()).status_code == 201
    updated_payload = agent_payload(
        tool_ids=[
            "system.get_current_time",
            "system.get_runtime_context",
            "system.get_current_time",
        ]
    )
    updated = client.put(
        "/api/agents/reservoir-dispatch",
        json={key: value for key, value in updated_payload.items() if key != "id"},
    )

    assert updated.status_code == 200
    assert updated.json()["tool_ids"] == BUILTIN_TOOL_IDS
    copied = client.post(
        "/api/agents/reservoir-dispatch/copy",
        json={"id": "tool-copy", "name": "工具副本", "copy_skills": False},
    )
    assert copied.status_code == 201
    assert copied.json()["tool_ids"] == BUILTIN_TOOL_IDS


def test_disabled_tool_binding_remains_readable_but_cannot_be_resaved(client):
    created = client.post(
        "/api/agents",
        json=agent_payload(tool_ids=["system.get_current_time"]),
    )
    assert created.status_code == 201
    client.app.state.agent_service.tool_service.store.set_enabled(
        "system.get_current_time",
        False,
    )

    readable = client.get("/api/agents/reservoir-dispatch")
    assert readable.status_code == 200
    assert readable.json()["tool_ids"] == ["system.get_current_time"]
    payload = {
        key: value
        for key, value in readable.json().items()
        if key in AgentConfig.model_fields
    }
    saved = client.put("/api/agents/reservoir-dispatch", json=payload)
    assert saved.status_code == 422


def test_builtin_repair_uses_locked_latest_config_during_concurrent_update(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    store = AgentStore(session_factory)
    AgentService(store, workspace_root=tmp_path / "agent-workspaces")

    stale = store.get(BUILTIN_AGENT_ID)
    stale["system_prompt"] = "过期提示词"
    stale["model"] = "stale-model"
    stale["skill_names"] = []
    stale["tool_ids"] = []

    latest_config = {
        name: store.get(BUILTIN_AGENT_ID)[name] for name in AgentConfig.model_fields
    }
    latest_config["system_prompt"] = "管理员最新提示词"
    latest_config["model"] = "latest-model"
    latest_config["skill_names"] = ["concurrently-added-skill"]
    latest_config["tool_ids"] = []
    store.update(BUILTIN_AGENT_ID, latest_config)

    repaired = AgentService(
        StaleBuiltinSnapshotStore(session_factory, stale),
        workspace_root=tmp_path / "agent-workspaces",
    ).get(BUILTIN_AGENT_ID)

    assert repaired.system_prompt == "管理员最新提示词"
    assert repaired.model == "latest-model"
    assert repaired.skill_names == ["concurrently-added-skill"]
    assert repaired.tool_ids == BUILTIN_TOOL_IDS
    assert repaired.enabled is True


def test_repairs_legacy_builtin_tool_bindings_without_removing_others(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    store = AgentStore(session_factory)
    service = AgentService(store, workspace_root=tmp_path / "agent-workspaces")
    builtin = store.get(BUILTIN_AGENT_ID)
    legacy_config = {name: builtin[name] for name in AgentConfig.model_fields}
    legacy_config["tool_ids"] = ["legacy.external_tool"]
    store.update(BUILTIN_AGENT_ID, legacy_config)

    repaired = AgentService(
        store,
        workspace_root=tmp_path / "agent-workspaces",
    ).get(BUILTIN_AGENT_ID)

    assert repaired.tool_ids == ["legacy.external_tool", *BUILTIN_TOOL_IDS]


def test_creates_and_lists_runtime_specific_agent(client):
    created = client.post("/api/agents", json=agent_payload())
    assert created.status_code == 201
    body = created.json()
    assert body["id"] == "reservoir-dispatch"
    assert body["runtime_form"] == "desktop"
    assert body["context_prompt"].startswith("结合本地工程文件")
    assert body["skill_names"] == ["flood-forecast"]
    assert body["startup_status"] == "ready"
    assert body["workspace_dir"].endswith("reservoir-dispatch")

    listed = client.get("/api/agents")
    assert listed.status_code == 200
    assert {agent["id"] for agent in listed.json()} == {
        BUILTIN_AGENT_ID,
        body["id"],
    }


def test_initializes_one_enabled_builtin_default_agent(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    store = AgentStore(session_factory)

    first = AgentService(store, workspace_root=tmp_path / "agent-workspaces")
    agents = first.list()

    assert len(agents) == 1
    assert agents[0].id == BUILTIN_AGENT_ID
    assert agents[0].is_builtin is True
    assert agents[0].is_default is True
    assert agents[0].enabled is True

    second = AgentService(store, workspace_root=tmp_path / "agent-workspaces")
    assert [agent.id for agent in second.list()] == [BUILTIN_AGENT_ID]


def test_list_uses_one_default_pointer_snapshot_for_all_flags(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    store = CountingAgentStore(session_factory)
    service = AgentService(store, workspace_root=tmp_path / "agent-workspaces")
    service.create(AgentCreateRequest(**agent_payload(skill_names=[])))
    service.create(
        AgentCreateRequest(
            **agent_payload(
                id="flood-analysis",
                name="洪水分析智能体",
                skill_names=[],
            )
        )
    )
    store.default_pointer_reads = 0

    agents = service.list()

    assert len(agents) == 3
    assert sum(agent.is_default for agent in agents) == 1
    assert store.default_pointer_reads == 2


def test_get_default_repairs_missing_or_invalid_pointer(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    store = AgentStore(session_factory)
    service = AgentService(store, workspace_root=tmp_path / "agent-workspaces")

    with session_factory.begin() as session:
        session.execute(
            update(PlatformSettingRecord)
            .where(PlatformSettingRecord.setting_key == DEFAULT_SETTING_KEY)
            .values(value={"agent_id": "missing-agent", "scope": "platform"})
        )

    assert service.get_default().id == BUILTIN_AGENT_ID
    pointer = store.get_default_id()
    assert pointer.agent_id == BUILTIN_AGENT_ID

    with session_factory.begin() as session:
        session.delete(session.get(PlatformSettingRecord, DEFAULT_SETTING_KEY))

    assert service.get_default().id == BUILTIN_AGENT_ID
    assert store.get_default_id().agent_id == BUILTIN_AGENT_ID


@pytest.mark.parametrize(
    "malformed_value",
    [
        {"agent_id": BUILTIN_AGENT_ID},
        {"agent_id": BUILTIN_AGENT_ID, "scope": "tenant"},
        [BUILTIN_AGENT_ID],
    ],
)
def test_get_default_repairs_malformed_pointer_scope_and_shape(
    tmp_path,
    malformed_value,
):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    store = AgentStore(session_factory)
    service = AgentService(store, workspace_root=tmp_path / "agent-workspaces")

    with session_factory.begin() as session:
        row = session.get(PlatformSettingRecord, DEFAULT_SETTING_KEY)
        original_version = row.version
        row.value = malformed_value

    malformed = store.get_default_id()
    assert malformed.agent_id is None
    assert malformed.version == original_version

    assert service.get_default().id == BUILTIN_AGENT_ID
    with session_factory() as session:
        repaired = session.get(PlatformSettingRecord, DEFAULT_SETTING_KEY)
        assert repaired.value == {
            "agent_id": BUILTIN_AGENT_ID,
            "scope": "platform",
        }
        assert repaired.version == original_version + 1


def test_get_default_falls_back_when_configured_agent_is_disabled(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    store = AgentStore(session_factory)
    service = AgentService(store, workspace_root=tmp_path / "agent-workspaces")
    service.create(AgentCreateRequest(**agent_payload(skill_names=[])))
    service.set_enabled("reservoir-dispatch", False)
    pointer = store.get_default_id()
    store.set_default_id("reservoir-dispatch", expected_version=pointer.version)

    default = service.get_default()

    assert default.id == BUILTIN_AGENT_ID
    assert default.is_default is True
    assert store.get_default_id().agent_id == BUILTIN_AGENT_ID


def test_restores_historically_disabled_builtin_agent(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    store = AgentStore(session_factory)
    service = AgentService(store, workspace_root=tmp_path / "agent-workspaces")
    builtin = store.get(BUILTIN_AGENT_ID)
    config = {name: builtin[name] for name in AgentConfig.model_fields}
    config["enabled"] = False
    store.update(BUILTIN_AGENT_ID, config)

    restored = AgentService(
        store,
        workspace_root=tmp_path / "agent-workspaces",
    ).get(BUILTIN_AGENT_ID)

    assert restored.enabled is True
    assert restored.is_builtin is True
    assert restored.is_default is True


def test_default_request_validates_agent_id():
    assert AgentDefaultRequest(agent_id="reservoir-dispatch").agent_id == (
        "reservoir-dispatch"
    )
    with pytest.raises(ValidationError):
        AgentDefaultRequest(agent_id="Invalid Agent ID")


def test_service_delegates_mutations_to_atomic_store_operations(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    store = AtomicOperationSpyStore(session_factory)
    service = AgentService(store, workspace_root=tmp_path / "agent-workspaces")
    service.create(AgentCreateRequest(**agent_payload(skill_names=[])))
    store.reject_legacy_mutations = True

    service.set_default("reservoir-dispatch")
    service.set_default(BUILTIN_AGENT_ID)
    service.update(
        "reservoir-dispatch",
        AgentConfig(
            **agent_payload(
                name="更新后的调度智能体",
                skill_names=[],
            )
        ),
    )
    service.set_enabled("reservoir-dispatch", False)
    service.set_enabled("reservoir-dispatch", True)
    service.delete("reservoir-dispatch")

    assert store.atomic_calls == [
        ("set_default_agent", "reservoir-dispatch"),
        ("set_default_agent", BUILTIN_AGENT_ID),
        ("update_agent", "reservoir-dispatch"),
        ("set_enabled_agent", "reservoir-dispatch"),
        ("set_enabled_agent", "reservoir-dispatch"),
        ("delete_agent", "reservoir-dispatch"),
    ]


def test_store_atomically_rejects_protected_default_mutations(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    store = AgentStore(session_factory)
    service = AgentService(store, workspace_root=tmp_path / "agent-workspaces")
    service.create(AgentCreateRequest(**agent_payload(skill_names=[])))
    pointer = store.get_default_id()
    store.set_default_agent(
        "reservoir-dispatch",
        expected_version=pointer.version,
    )
    current = store.get("reservoir-dispatch")
    disabled_config = {name: current[name] for name in AgentConfig.model_fields}
    disabled_config["enabled"] = False

    with pytest.raises(ValueError, match="Default agent"):
        store.set_enabled_agent("reservoir-dispatch", False)
    with pytest.raises(ValueError, match="Default agent"):
        store.update_agent("reservoir-dispatch", disabled_config)
    with pytest.raises(ValueError, match="Default agent"):
        store.delete_agent(
            "reservoir-dispatch",
            builtin_agent_id=BUILTIN_AGENT_ID,
        )

    preserved = store.get("reservoir-dispatch")
    assert preserved is not None
    assert preserved["enabled"] is True
    assert store.get_default_id().agent_id == "reservoir-dispatch"


def test_store_permanently_protects_builtin_delete_in_same_transaction(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    store = AgentStore(session_factory)
    service = AgentService(store, workspace_root=tmp_path / "agent-workspaces")
    service.create(AgentCreateRequest(**agent_payload(skill_names=[])))
    pointer = store.get_default_id()
    store.set_default_agent(
        "reservoir-dispatch",
        expected_version=pointer.version,
    )

    with pytest.raises(ValueError, match="Built-in agent"):
        store.delete_agent(
            BUILTIN_AGENT_ID,
            builtin_agent_id=BUILTIN_AGENT_ID,
        )

    assert store.get(BUILTIN_AGENT_ID) is not None
    assert store.get_default_id().agent_id == "reservoir-dispatch"


def test_store_default_switch_validates_target_before_pointer_commit(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    store = AgentStore(session_factory)
    service = AgentService(store, workspace_root=tmp_path / "agent-workspaces")
    service.create(
        AgentCreateRequest(
            **agent_payload(
                id="disabled-agent",
                name="停用智能体",
                skill_names=[],
                enabled=False,
            )
        )
    )

    pointer = store.get_default_id()
    with pytest.raises(ValueError, match="Disabled agent"):
        store.set_default_agent(
            "disabled-agent",
            expected_version=pointer.version,
        )
    with pytest.raises(ValueError, match="not found"):
        store.set_default_agent(
            "missing-agent",
            expected_version=pointer.version,
        )

    assert store.get_default_id() == pointer


def test_store_rejects_stale_default_switch_without_overwriting_winner(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    store = AgentStore(session_factory)
    service = AgentService(store, workspace_root=tmp_path / "agent-workspaces")
    service.create(AgentCreateRequest(**agent_payload(skill_names=[])))
    stale_pointer = store.get_default_id()
    store.set_default_agent(
        "reservoir-dispatch",
        expected_version=stale_pointer.version,
    )

    with pytest.raises(AgentConcurrentUpdateError):
        store.set_default_agent(
            BUILTIN_AGENT_ID,
            expected_version=stale_pointer.version,
        )

    assert store.get_default_id().agent_id == "reservoir-dispatch"


def test_gets_effective_default_agent(client):
    response = client.get("/api/agents/default")

    assert response.status_code == 200
    assert response.json()["id"] == BUILTIN_AGENT_ID
    assert response.json()["is_default"] is True


def test_switches_default_to_enabled_agent_atomically(client):
    assert client.post("/api/agents", json=agent_payload()).status_code == 201
    switched = client.put(
        "/api/agents/default",
        json={"agent_id": "reservoir-dispatch"},
    )

    assert switched.status_code == 200
    assert switched.json()["id"] == "reservoir-dispatch"
    assert switched.json()["is_default"] is True
    listed = client.get("/api/agents")
    defaults = [agent["id"] for agent in listed.json() if agent["is_default"]]
    assert defaults == ["reservoir-dispatch"]


def test_rejects_disabled_or_missing_default_target(client):
    assert (
        client.post(
        "/api/agents",
        json=agent_payload(enabled=False),
        ).status_code
        == 201
    )

    disabled = client.put(
        "/api/agents/default",
        json={"agent_id": "reservoir-dispatch"},
    )
    assert disabled.status_code == 422
    missing = client.put(
        "/api/agents/default",
        json={"agent_id": "missing-agent"},
    )
    assert missing.status_code == 404
    assert client.get("/api/agents/default").json()["id"] == BUILTIN_AGENT_ID


def test_rejects_deleting_or_disabling_active_default(client):
    assert client.delete(f"/api/agents/{BUILTIN_AGENT_ID}").status_code == 409
    disabled = client.patch(
        f"/api/agents/{BUILTIN_AGENT_ID}/toggle",
        json={"enabled": False},
    )
    assert disabled.status_code == 409
    enabled = client.patch(
        f"/api/agents/{BUILTIN_AGENT_ID}/toggle",
        json={"enabled": True},
    )
    assert enabled.status_code == 200
    assert enabled.json()["is_default"] is True


def test_old_ordinary_default_can_be_disabled_and_deleted_after_switch(client):
    assert client.post("/api/agents", json=agent_payload()).status_code == 201
    assert (
        client.put(
        "/api/agents/default",
        json={"agent_id": "reservoir-dispatch"},
        ).status_code
        == 200
    )
    assert (
        client.patch(
        "/api/agents/reservoir-dispatch/toggle",
        json={"enabled": False},
        ).status_code
        == 409
    )

    assert (
        client.put(
        "/api/agents/default",
        json={"agent_id": BUILTIN_AGENT_ID},
        ).status_code
        == 200
    )
    assert (
        client.patch(
        "/api/agents/reservoir-dispatch/toggle",
        json={"enabled": False},
        ).status_code
        == 200
    )
    assert client.delete("/api/agents/reservoir-dispatch").status_code == 200


def test_builtin_agent_cannot_be_deleted_after_default_switch(client):
    assert client.post("/api/agents", json=agent_payload()).status_code == 201
    assert (
        client.put(
        "/api/agents/default",
        json={"agent_id": "reservoir-dispatch"},
        ).status_code
        == 200
    )

    deleted = client.delete(f"/api/agents/{BUILTIN_AGENT_ID}")

    assert deleted.status_code == 409
    assert client.get(f"/api/agents/{BUILTIN_AGENT_ID}").status_code == 200


def test_maps_concurrent_default_update_to_conflict(client, monkeypatch):
    assert client.post("/api/agents", json=agent_payload()).status_code == 201

    def reject_stale_update(session, agent_id, expected_version):
        raise AgentConcurrentUpdateError(
            "Default agent changed concurrently; retry the request"
        )

    monkeypatch.setattr(
        client.app.state.agent_service.store,
        "set_default_agent_in_session",
        reject_stale_update,
    )
    response = client.put(
        "/api/agents/default",
        json={"agent_id": "reservoir-dispatch"},
    )

    assert response.status_code == 409
    assert "changed concurrently" in response.json()["detail"]


def test_rejects_unknown_skills_and_duplicate_id(client):
    missing = client.post(
        "/api/agents", json=agent_payload(skill_names=["missing-skill"])
    )
    assert missing.status_code == 422
    assert "missing-skill" in missing.text

    assert client.post("/api/agents", json=agent_payload()).status_code == 201
    duplicate = client.post("/api/agents", json=agent_payload())
    assert duplicate.status_code == 409


def test_updates_toggles_and_pins_agent(client):
    client.post("/api/agents", json=agent_payload())
    updated = client.put(
        "/api/agents/reservoir-dispatch",
        json=agent_payload(
            name="水库联合调度智能体",
            runtime_form="web",
            context_prompt="结合当前页面的水库对象与告警信息回答。",
        )
        | {"id": None},
    )
    assert updated.status_code == 200
    assert updated.json()["runtime_form"] == "web"
    assert updated.json()["name"] == "水库联合调度智能体"

    disabled = client.patch(
        "/api/agents/reservoir-dispatch/toggle", json={"enabled": False}
    )
    assert disabled.status_code == 200
    assert disabled.json()["startup_status"] == "disabled"

    pinned = client.patch("/api/agents/reservoir-dispatch/pin", json={"pinned": True})
    assert pinned.status_code == 200
    assert pinned.json()["pinned"] is True


def test_copies_agent_with_independent_identity(client):
    client.post("/api/agents", json=agent_payload())
    copied = client.post(
        "/api/agents/reservoir-dispatch/copy",
        json={
            "id": "reservoir-dispatch-copy",
            "name": "水库调度智能体副本",
            "copy_skills": True,
        },
    )
    assert copied.status_code == 201
    assert copied.json()["id"] == "reservoir-dispatch-copy"
    assert copied.json()["skill_names"] == ["flood-forecast"]
    assert copied.json()["pinned"] is False
    assert copied.json()["enabled"] is False
    assert copied.json()["is_default"] is False


def test_deletes_agent(client):
    client.post("/api/agents", json=agent_payload())
    service = client.app.state.agent_service
    workspace = service.workspace_root / "reservoir-dispatch"
    deleted = client.delete("/api/agents/reservoir-dispatch")
    assert deleted.status_code == 200
    assert client.get("/api/agents/reservoir-dispatch").status_code == 404
    assert not workspace.exists()
    assert not list(service.workspace_root.glob(".reservoir-dispatch.quarantine-*"))


def test_create_rolls_back_agent_and_workspace_when_audit_fails(tmp_path):
    from app.audit.recorder import AuditRecorder
    from app.core.request_context import RequestContext

    class FailingRecorder(AuditRecorder):
        def record(self, session, request):
            raise RuntimeError("audit unavailable")

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'agent-audit.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    store = AgentStore(factory)
    service = AgentService(
        store, workspace_root=tmp_path / "workspaces", audit_recorder=FailingRecorder()
    )
    context = RequestContext(unit_id="unit-1", project_id="p1", user_id="u1")
    with factory() as session, pytest.raises(RuntimeError, match="audit unavailable"):
        service.create(
            AgentCreateRequest(**agent_payload(skill_names=[])),
            context=context,
            session=session,
            request_id="agent-create-fail",
        )
    assert store.get("reservoir-dispatch") is None
    assert not (tmp_path / "workspaces" / "reservoir-dispatch").exists()


def test_delete_restores_quarantined_workspace_when_audit_fails(tmp_path):
    from app.audit.recorder import AuditRecorder
    from app.core.request_context import RequestContext

    class FailingRecorder(AuditRecorder):
        def record(self, session, request):
            raise RuntimeError("audit unavailable")

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'agent-delete-audit.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    store = AgentStore(factory)
    root = tmp_path / "workspaces"
    service = AgentService(store, workspace_root=root)
    service.create(AgentCreateRequest(**agent_payload(skill_names=[])))
    workspace = root / "reservoir-dispatch"
    assert workspace.is_dir()
    failing = AgentService(store, workspace_root=root, audit_recorder=FailingRecorder())
    context = RequestContext(unit_id="unit-1", project_id="p1", user_id="u1")
    with factory() as session, pytest.raises(RuntimeError, match="audit unavailable"):
        failing.delete(
            "reservoir-dispatch",
            context=context,
            session=session,
            request_id="agent-delete-fail",
        )
    assert store.get("reservoir-dispatch") is not None
    assert workspace.is_dir()
    assert not list(root.glob(".reservoir-dispatch.quarantine-*"))


def test_agent_create_route_requires_request_context(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'agent-auth.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app = FastAPI()
    app.state.allow_dev_identity = True
    app.include_router(
        create_router(
            AgentService(AgentStore(factory), workspace_root=tmp_path / "workspaces")
        ),
        prefix="/api/agents",
    )
    with TestClient(app) as unauthenticated:
        response = unauthenticated.post(
            "/api/agents", json=agent_payload(skill_names=[])
        )
    assert response.status_code == 401


def test_protected_agent_delete_records_failed_audit_in_fresh_transaction(client):
    from sqlalchemy import select
    from app.audit.models import AuditEvent

    response = client.delete(
        f"/api/agents/{BUILTIN_AGENT_ID}",
        headers={**AUTH_HEADERS, "X-Request-ID": "protected-delete-1"},
    )
    assert response.status_code == 409
    factory = client.app.state.agent_service.store.session_factory
    with factory() as session:
        event = session.scalar(select(AuditEvent))
    assert event.status == "failed"
    assert event.action == "resource.deleted"
    assert event.source == "agent"
    assert event.error_code == "AGENT_PROTECTED"
    assert event.resource_id == BUILTIN_AGENT_ID
    assert event.metadata_json == {}
    assert event.trace_id == "protected-delete-1"


def test_invalid_agent_body_records_failed_audit_without_request_secrets(client):
    from sqlalchemy import select
    from app.audit.models import AuditEvent

    secret = "Bearer agent-validation-secret"
    payload = agent_payload(skill_names=[])
    payload.pop("name")
    payload["system_prompt"] = secret
    response = client.post(
        "/api/agents",
        json=payload,
        headers={**AUTH_HEADERS, "X-Request-ID": "agent-validation-1"},
    )
    assert response.status_code == 422
    factory = client.app.state.agent_service.store.session_factory
    with factory() as session:
        event = session.scalar(select(AuditEvent))
    assert event.source == "agent"
    assert event.action == "resource.created"
    assert event.resource_type == "agent"
    assert event.resource_id == "agent"
    assert event.unit_id == "unit-1"
    assert event.project_id == "p1"
    assert event.user_id == "u1"
    assert event.error_code == "REQUEST_VALIDATION"
    assert event.metadata_json == {}
    assert secret not in f"{event.summary} {event.metadata_json}"
    assert event.trace_id == "agent-validation-1"


def test_repeated_agent_pin_without_request_id_records_each_mutation(client):
    from sqlalchemy import select
    from app.audit.models import AuditEvent

    assert (
        client.post("/api/agents", json=agent_payload(skill_names=[])).status_code
        == 201
    )
    assert (
        client.patch(
            "/api/agents/reservoir-dispatch/pin", json={"pinned": True}
        ).status_code
        == 200
    )
    assert (
        client.patch(
            "/api/agents/reservoir-dispatch/pin", json={"pinned": False}
        ).status_code
        == 200
    )
    factory = client.app.state.agent_service.store.session_factory
    with factory() as session:
        events = list(
            session.scalars(
                select(AuditEvent).where(
            AuditEvent.source == "agent",
            AuditEvent.action == "resource.updated",
            AuditEvent.resource_id == "reservoir-dispatch",
                )
            )
        )
    assert len(events) == 2
    assert len({event.idempotency_key for event in events}) == 2


def test_update_restores_workspace_after_partial_write_failure(tmp_path, monkeypatch):
    from pathlib import Path
    from app.core.request_context import RequestContext

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'agent-partial-write.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    store = AgentStore(factory)
    service = AgentService(store, workspace_root=tmp_path / "workspaces")
    service.create(AgentCreateRequest(**agent_payload(skill_names=[])))
    agents_file = tmp_path / "workspaces" / "reservoir-dispatch" / "AGENTS.md"
    previous = agents_file.read_bytes()
    original_write_bytes = Path.write_bytes

    def partial_write_then_fail(path, data, *args, **kwargs):
        if path.parent == agents_file.parent and path.name.startswith(
            ".AGENTS.md.tmp-"
        ):
            original_write_bytes(path, b"partial")
            raise OSError("disk write failed")
        return original_write_bytes(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_bytes", partial_write_then_fail)
    updated = AgentConfig(**agent_payload(name="Changed", skill_names=[]))
    context = RequestContext(unit_id="unit-1", project_id="p1", user_id="u1")
    with factory() as session, pytest.raises(OSError, match="disk write failed"):
        service.update(
            "reservoir-dispatch",
            updated,
            context=context,
            session=session,
            request_id="partial-write-1",
        )
    assert agents_file.read_bytes() == previous
    assert store.get("reservoir-dispatch")["name"] != "Changed"


def test_same_client_request_id_does_not_deduplicate_distinct_http_requests(client):
    from sqlalchemy import select
    from app.audit.models import AuditEvent

    assert (
        client.post("/api/agents", json=agent_payload(skill_names=[])).status_code
        == 201
    )
    headers = {"X-Request-ID": "shared-correlation"}
    assert (
        client.patch(
            "/api/agents/reservoir-dispatch/pin", json={"pinned": True}, headers=headers
        ).status_code
        == 200
    )
    assert (
        client.patch(
            "/api/agents/reservoir-dispatch/pin",
            json={"pinned": False},
            headers=headers,
        ).status_code
        == 200
    )
    factory = client.app.state.agent_service.store.session_factory
    with factory() as session:
        events = list(
            session.scalars(
                select(AuditEvent).where(
            AuditEvent.source == "agent",
            AuditEvent.action == "resource.updated",
            AuditEvent.resource_id == "reservoir-dispatch",
                )
            )
        )
    assert len(events) == 2
    assert {event.trace_id for event in events} == {"shared-correlation"}
    assert len({event.idempotency_key for event in events}) == 2
    assert all("shared-correlation" not in event.idempotency_key for event in events)


def test_same_correlation_preserves_fail_then_success_statuses(client):
    from sqlalchemy import select
    from app.audit.models import AuditEvent

    headers = {"X-Request-ID": "retry-correlation"}
    failed = client.patch(
        "/api/agents/retry-agent/pin", json={"pinned": True}, headers=headers
    )
    assert failed.status_code == 404
    payload = agent_payload(id="retry-agent", skill_names=[])
    assert client.post("/api/agents", json=payload).status_code == 201
    succeeded = client.patch(
        "/api/agents/retry-agent/pin", json={"pinned": True}, headers=headers
    )
    assert succeeded.status_code == 200
    factory = client.app.state.agent_service.store.session_factory
    with factory() as session:
        events = list(
            session.scalars(
                select(AuditEvent).where(
            AuditEvent.action == "resource.updated",
            AuditEvent.resource_id == "retry-agent",
                )
            )
        )
    assert {event.status for event in events} == {"failed", "succeeded"}
    assert {event.trace_id for event in events} == {"retry-correlation"}


def test_failed_audit_is_best_effort_and_invalid_request_id_is_safe(client):
    from app.audit.recorder import AuditRecorder

    class FailingRecorder(AuditRecorder):
        def record(self, session, request):
            raise RuntimeError("database secret must not escape")

    client.app.state.agent_service.audit_recorder = FailingRecorder()
    response = client.patch(
        "/api/agents/missing-agent/pin",
        json={"pinned": True},
        headers={"X-Request-ID": "x" * 1000},
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_update_rejects_workspace_outside_root_without_touching_file(client, tmp_path):
    from app.db.platform_models import ManagedAgentRecord

    assert (
        client.post("/api/agents", json=agent_payload(skill_names=[])).status_code
        == 201
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "AGENTS.md"
    target.write_text("outside-safe", encoding="utf-8")
    factory = client.app.state.agent_service.store.session_factory
    with factory.begin() as session:
        row = session.get(ManagedAgentRecord, "reservoir-dispatch")
        row.workspace_dir = str(outside)
    response = client.put(
        "/api/agents/reservoir-dispatch",
        json=agent_payload(name="Changed", skill_names=[]),
    )
    assert response.status_code == 422
    assert target.read_text(encoding="utf-8") == "outside-safe"


def test_update_rejects_agents_file_symlink(client, tmp_path):
    assert (
        client.post("/api/agents", json=agent_payload(skill_names=[])).status_code
        == 201
    )
    workspace = tmp_path / "agent-workspaces" / "reservoir-dispatch"
    agents_file = workspace / "AGENTS.md"
    outside = tmp_path / "outside-agents.md"
    outside.write_text("outside-safe", encoding="utf-8")
    agents_file.unlink()
    try:
        agents_file.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    response = client.put(
        "/api/agents/reservoir-dispatch",
        json=agent_payload(name="Changed", skill_names=[]),
    )
    assert response.status_code == 422
    assert outside.read_text(encoding="utf-8") == "outside-safe"


def test_delete_cleanup_failure_keeps_successful_business_result(client, monkeypatch):
    from sqlalchemy import select
    from app.audit.models import AuditEvent
    import app.agents.service as agent_service_module

    assert (
        client.post("/api/agents", json=agent_payload(skill_names=[])).status_code
        == 201
    )
    original_rmtree = agent_service_module.shutil.rmtree

    def fail_quarantine_cleanup(path, *args, **kwargs):
        if ".quarantine-" in str(path):
            raise OSError("cleanup failed")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(agent_service_module.shutil, "rmtree", fail_quarantine_cleanup)
    response = client.delete("/api/agents/reservoir-dispatch")
    assert response.status_code == 200
    assert client.app.state.agent_service.store.get("reservoir-dispatch") is None
    factory = client.app.state.agent_service.store.session_factory
    with factory() as session:
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.action == "resource.deleted")
        )
    assert event.status == "succeeded"


def test_delete_restore_failure_does_not_mask_audit_error(client, monkeypatch):
    from pathlib import Path
    from app.audit.recorder import AuditRecorder

    class FailingRecorder(AuditRecorder):
        def record(self, session, request):
            raise RuntimeError("audit unavailable")

    assert (
        client.post("/api/agents", json=agent_payload(skill_names=[])).status_code
        == 201
    )
    service = client.app.state.agent_service
    service.audit_recorder = FailingRecorder()
    original_rename = Path.rename

    def fail_restore(path, target):
        if ".quarantine-" in path.name:
            raise OSError("restore failed")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_restore)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        client.delete("/api/agents/reservoir-dispatch")


def test_same_correlation_preserves_success_then_fail_statuses(client):
    from sqlalchemy import select
    from app.audit.models import AuditEvent

    assert client.post("/api/agents", json=agent_payload(skill_names=[])).status_code == 201
    headers = {"X-Request-ID": "success-fail-correlation"}
    assert client.patch("/api/agents/reservoir-dispatch/pin", json={"pinned": True}, headers=headers).status_code == 200
    assert client.delete("/api/agents/reservoir-dispatch").status_code == 200
    assert client.patch("/api/agents/reservoir-dispatch/pin", json={"pinned": False}, headers=headers).status_code == 404
    factory = client.app.state.agent_service.store.session_factory
    with factory() as session:
        events = list(session.scalars(select(AuditEvent).where(
            AuditEvent.action == "resource.updated",
            AuditEvent.resource_id == "reservoir-dispatch",
            AuditEvent.trace_id == "success-fail-correlation",
        )))
    assert {event.status for event in events} == {"succeeded", "failed"}


def test_update_rejects_workspace_directory_symlink(client, tmp_path):
    from app.db.platform_models import ManagedAgentRecord

    assert client.post("/api/agents", json=agent_payload(skill_names=[])).status_code == 201
    real_workspace = tmp_path / "agent-workspaces" / "reservoir-dispatch"
    linked_workspace = tmp_path / "linked-workspace"
    try:
        linked_workspace.symlink_to(real_workspace, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")
    factory = client.app.state.agent_service.store.session_factory
    with factory.begin() as session:
        session.get(ManagedAgentRecord, "reservoir-dispatch").workspace_dir = str(linked_workspace)
    before = (real_workspace / "AGENTS.md").read_bytes()
    response = client.put("/api/agents/reservoir-dispatch", json=agent_payload(name="Changed", skill_names=[]))
    assert response.status_code == 422
    assert (real_workspace / "AGENTS.md").read_bytes() == before


def test_update_restores_database_when_atomic_replace_fails(client, monkeypatch):
    import app.agents.service as agent_service_module

    assert client.post("/api/agents", json=agent_payload(skill_names=[])).status_code == 201
    service = client.app.state.agent_service
    workspace = service.workspace_root / "reservoir-dispatch"
    agents_file = workspace / "AGENTS.md"
    previous = agents_file.read_bytes()
    original_replace = agent_service_module.os.replace

    def fail_agents_replace(source, target):
        if target == agents_file:
            raise OSError("replace failed")
        return original_replace(source, target)

    monkeypatch.setattr(agent_service_module.os, "replace", fail_agents_replace)
    with pytest.raises(OSError, match="replace failed"):
        client.put("/api/agents/reservoir-dispatch", json=agent_payload(name="Changed", skill_names=[]))
    assert agents_file.read_bytes() == previous
    assert service.store.get("reservoir-dispatch")["name"] != "Changed"
