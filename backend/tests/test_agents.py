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
from app.db.platform_models import PlatformSettingRecord
from app.skills.schemas import SkillCreateRequest
from app.skills.service import SkillService


class CountingAgentStore(AgentStore):
    def __init__(self, session_factory):
        super().__init__(session_factory)
        self.default_pointer_reads = 0

    def get_default_id(self):
        self.default_pointer_reads += 1
        return super().get_default_id()


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
            content='''---
name: flood-forecast
description: 洪水预报
version: "1.0"
---
# 洪水预报
''',
            tags=["水文"],
        )
    )
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(engine)
    store = AgentStore(sessionmaker(bind=engine, expire_on_commit=False, class_=Session))
    service = AgentService(store, skill_service=skill_service, workspace_root=tmp_path / "agent-workspaces")
    app = FastAPI()
    app.include_router(create_router(service), prefix="/api/agents")
    app.state.agent_service = service
    return TestClient(app)


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
        "enabled": True,
    }
    payload.update(overrides)
    return payload


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
    assert client.post(
        "/api/agents",
        json=agent_payload(enabled=False),
    ).status_code == 201

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
    assert client.put(
        "/api/agents/default",
        json={"agent_id": "reservoir-dispatch"},
    ).status_code == 200
    assert client.patch(
        "/api/agents/reservoir-dispatch/toggle",
        json={"enabled": False},
    ).status_code == 409

    assert client.put(
        "/api/agents/default",
        json={"agent_id": BUILTIN_AGENT_ID},
    ).status_code == 200
    assert client.patch(
        "/api/agents/reservoir-dispatch/toggle",
        json={"enabled": False},
    ).status_code == 200
    assert client.delete("/api/agents/reservoir-dispatch").status_code == 200


def test_builtin_agent_cannot_be_deleted_after_default_switch(client):
    assert client.post("/api/agents", json=agent_payload()).status_code == 201
    assert client.put(
        "/api/agents/default",
        json={"agent_id": "reservoir-dispatch"},
    ).status_code == 200

    deleted = client.delete(f"/api/agents/{BUILTIN_AGENT_ID}")

    assert deleted.status_code == 409
    assert client.get(f"/api/agents/{BUILTIN_AGENT_ID}").status_code == 200


def test_maps_concurrent_default_update_to_conflict(client, monkeypatch):
    assert client.post("/api/agents", json=agent_payload()).status_code == 201

    def reject_stale_update(agent_id, expected_version):
        raise AgentConcurrentUpdateError(
            "Default agent changed concurrently; retry the request"
        )

    monkeypatch.setattr(
        client.app.state.agent_service.store,
        "set_default_agent",
        reject_stale_update,
    )
    response = client.put(
        "/api/agents/default",
        json={"agent_id": "reservoir-dispatch"},
    )

    assert response.status_code == 409
    assert "changed concurrently" in response.json()["detail"]


def test_rejects_unknown_skills_and_duplicate_id(client):
    missing = client.post("/api/agents", json=agent_payload(skill_names=["missing-skill"]))
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
        ) | {"id": None},
    )
    assert updated.status_code == 200
    assert updated.json()["runtime_form"] == "web"
    assert updated.json()["name"] == "水库联合调度智能体"

    disabled = client.patch("/api/agents/reservoir-dispatch/toggle", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["startup_status"] == "disabled"

    pinned = client.patch("/api/agents/reservoir-dispatch/pin", json={"pinned": True})
    assert pinned.status_code == 200
    assert pinned.json()["pinned"] is True


def test_copies_agent_with_independent_identity(client):
    client.post("/api/agents", json=agent_payload())
    copied = client.post(
        "/api/agents/reservoir-dispatch/copy",
        json={"id": "reservoir-dispatch-copy", "name": "水库调度智能体副本", "copy_skills": True},
    )
    assert copied.status_code == 201
    assert copied.json()["id"] == "reservoir-dispatch-copy"
    assert copied.json()["skill_names"] == ["flood-forecast"]
    assert copied.json()["pinned"] is False
    assert copied.json()["enabled"] is False
    assert copied.json()["is_default"] is False


def test_deletes_agent(client):
    client.post("/api/agents", json=agent_payload())
    deleted = client.delete("/api/agents/reservoir-dispatch")
    assert deleted.status_code == 200
    assert client.get("/api/agents/reservoir-dispatch").status_code == 404
