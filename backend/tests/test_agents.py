import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session, sessionmaker

from app.agents.router import create_router
from app.agents.schemas import AgentConfig, AgentCreateRequest, AgentDefaultRequest
from app.agents.service import BUILTIN_AGENT_ID, AgentService
from app.agents.store import DEFAULT_SETTING_KEY, AgentStore
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


def test_deletes_agent(client):
    client.post("/api/agents", json=agent_payload())
    deleted = client.delete("/api/agents/reservoir-dispatch")
    assert deleted.status_code == 200
    assert client.get("/api/agents/reservoir-dispatch").status_code == 404
