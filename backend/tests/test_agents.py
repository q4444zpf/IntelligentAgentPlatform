import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.agents.router import create_router
from app.agents.service import AgentService
from app.agents.store import AgentStore
from app.db.base import Base
from app.skills.schemas import SkillCreateRequest
from app.skills.service import SkillService


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
    assert listed.json() == [body]


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
