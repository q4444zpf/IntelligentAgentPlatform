from types import SimpleNamespace

import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.agents.service import BUILTIN_AGENT_ID, AgentNotFoundError
from app.conversations.dispatcher import UnavailableRunDispatcher
from app.conversations.models import AgentRun, Message, ToolInvocation
from app.conversations.repository import ConversationRepository
from app.conversations.router import create_router
from app.conversations.service import ConversationService
from app.db.base import Base


class StubAgentService:
    def __init__(self):
        self.agents = {
            BUILTIN_AGENT_ID: SimpleNamespace(id=BUILTIN_AGENT_ID, enabled=True),
            "flood": SimpleNamespace(id="flood", enabled=True),
            "disabled-agent": SimpleNamespace(id="disabled-agent", enabled=False),
        }

    def get_default(self):
        return self.agents[BUILTIN_AGENT_ID]

    def get(self, agent_id: str):
        try:
            return self.agents[agent_id]
        except KeyError as error:
            raise AgentNotFoundError(agent_id) from error


def build_client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    service = ConversationService(
        ConversationRepository(session),
        UnavailableRunDispatcher(),
        agent_service=StubAgentService(),
    )
    app = FastAPI()
    app.state.allow_dev_identity = True
    app.state.conversation_session = session
    app.include_router(create_router(lambda _session: service), prefix="/api")
    return TestClient(app)


HEADERS = {"X-User-ID": "u1", "X-Project-ID": "p1"}


def create_run(client, *, headers=HEADERS, title="洪水研判", actor_id="flood"):
    conversation = client.post(
        "/api/conversations", json={"title": title}, headers=headers
    ).json()
    return client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "分析 洪峰", "actor_type": "agent", "actor_id": actor_id},
        headers=headers,
    ).json()


def test_agent_run_list_projects_items_and_summary():
    client = build_client()
    accepted = create_run(client, title="防洪调度")
    session = client.app.state.conversation_session
    run = session.get(AgentRun, accepted["run"]["id"])
    run.status = "completed"
    session.add(
        ToolInvocation(
            run_id=run.id,
            tool_call_id="call-1",
            tool_id="forecast",
            tool_version="1",
            status="completed",
            arguments_summary={},
        )
    )
    session.commit()

    response = client.get("/api/agent-runs", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert body["total"] == 1
    assert body["summary"] == {
        "total": 1,
        "completed": 1,
        "running": 0,
        "failed": 0,
        "tool_invocations": 1,
    }
    assert body["items"] == [
        {
            "id": run.id,
            "conversation_id": accepted["run"]["conversation_id"],
            "conversation_title": "防洪调度",
            "trigger_message_id": accepted["message"]["id"],
            "trigger_summary": "分析 洪峰",
            "actor_type": "agent",
            "actor_id": "flood",
            "status": "completed",
            "tool_invocation_count": 1,
            "duration_ms": 0,
            "created_at": body["items"][0]["created_at"],
            "updated_at": body["items"][0]["updated_at"],
        }
    ]


def test_agent_run_list_filters_by_status_actor_and_query():
    client = build_client()
    matched = create_run(client, title="闸门调度", actor_id="flood")
    create_run(client, title="水情会商", actor_id="builtin-assistant")
    session = client.app.state.conversation_session
    session.get(AgentRun, matched["run"]["id"]).status = "completed"
    session.commit()

    response = client.get(
        "/api/agent-runs",
        params={"status": "completed", "actor_id": "flood", "query": "闸门"},
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [
        matched["run"]["id"]
    ]
    assert response.json()["summary"]["total"] == 1


def test_agent_run_list_is_scoped_to_project_and_user():
    client = build_client()
    create_run(client, headers={"X-User-ID": "u2", "X-Project-ID": "p2"})

    response = client.get("/api/agent-runs", headers=HEADERS)

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "page": 1,
        "page_size": 20,
        "total": 0,
        "summary": {
            "total": 0,
            "completed": 0,
            "running": 0,
            "failed": 0,
            "tool_invocations": 0,
        },
    }


def test_agent_run_list_rejects_invalid_query_parameters():
    client = build_client()

    assert client.get("/api/agent-runs?page=0", headers=HEADERS).status_code == 422
    assert (
        client.get("/api/agent-runs?page_size=101", headers=HEADERS).status_code
        == 422
    )
    assert (
        client.get(
            "/api/agent-runs?started_after=not-a-time", headers=HEADERS
        ).status_code
        == 422
    )


@pytest.mark.parametrize("query", ["%", "_"])
def test_agent_run_list_treats_query_wildcards_as_literals(query):
    client = build_client()
    create_run(client, title="ordinary title")

    response = client.get(
        "/api/agent-runs", params={"query": query}, headers=HEADERS
    )

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 0


def test_agent_run_list_rejects_unknown_status():
    client = build_client()

    response = client.get(
        "/api/agent-runs", params={"status": "banana"}, headers=HEADERS
    )

    assert response.status_code == 422


def test_agent_run_list_treats_backslash_as_a_literal_without_match():
    client = build_client()
    create_run(client, title="ordinary title")

    response = client.get(
        "/api/agent-runs", params={"query": "\\"}, headers=HEADERS
    )

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_agent_run_list_matches_a_literal_backslash_in_title():
    client = build_client()
    accepted = create_run(client, title="Gate\\Dispatch")

    response = client.get(
        "/api/agent-runs", params={"query": "\\"}, headers=HEADERS
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [
        accepted["run"]["id"]
    ]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("status", "Invalid"),
        ("status", "_invalid"),
        ("status", "a" * 31),
        ("actor_id", "Invalid"),
        ("actor_id", "a" * 65),
        ("query", "q" * 201),
    ],
)
def test_agent_run_list_rejects_unsafe_or_oversized_filters(name, value):
    client = build_client()

    response = client.get(
        "/api/agent-runs", params={name: value}, headers=HEADERS
    )

    assert response.status_code == 422


def test_agent_run_list_rejects_naive_time_filters():
    client = build_client()

    response = client.get(
        "/api/agent-runs",
        params={"started_after": "2026-01-01T00:00:00"},
        headers=HEADERS,
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Run date filters must include a timezone"


def test_agent_run_list_rejects_reversed_time_range():
    client = build_client()

    response = client.get(
        "/api/agent-runs",
        params={
            "started_after": "2026-01-02T00:00:00Z",
            "started_before": "2026-01-01T00:00:00Z",
        },
        headers=HEADERS,
    )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "started_after must not be later than started_before"
    )


def test_message_creation_returns_202_and_run():
    client = build_client()
    conversation = client.post(
        "/api/conversations", json={"title": "洪水研判"}, headers=HEADERS
    ).json()
    response = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "分析洪峰", "actor_type": "agent", "actor_id": "flood"},
        headers=HEADERS,
    )
    assert response.status_code == 202
    assert response.json()["run"]["status"] == "queued"


def test_message_creation_uses_default_agent_when_actor_id_is_omitted():
    client = build_client()
    conversation = client.post(
        "/api/conversations", json={"title": "默认智能体"}, headers=HEADERS
    ).json()
    response = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "分析水情", "actor_type": "agent"},
        headers=HEADERS,
    )

    assert response.status_code == 202
    assert response.json()["run"]["actor_id"] == BUILTIN_AGENT_ID


def test_message_creation_preserves_explicit_enabled_agent():
    client = build_client()
    conversation = client.post(
        "/api/conversations", json={"title": "指定智能体"}, headers=HEADERS
    ).json()
    response = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "分析洪峰", "actor_type": "agent", "actor_id": "flood"},
        headers=HEADERS,
    )

    assert response.status_code == 202
    assert response.json()["run"]["actor_id"] == "flood"


def test_missing_explicit_agent_returns_422_without_persisting():
    client = build_client()
    conversation = client.post(
        "/api/conversations", json={"title": "缺失智能体"}, headers=HEADERS
    ).json()
    response = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={
            "content": "分析洪峰",
            "actor_type": "agent",
            "actor_id": "missing-agent",
        },
        headers=HEADERS,
    )

    assert response.status_code == 422
    session = client.app.state.conversation_session
    assert session.scalar(select(func.count()).select_from(Message)) == 0
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 0


def test_disabled_explicit_agent_returns_422_without_persisting():
    client = build_client()
    conversation = client.post(
        "/api/conversations", json={"title": "禁用智能体"}, headers=HEADERS
    ).json()
    response = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={
            "content": "分析洪峰",
            "actor_type": "agent",
            "actor_id": "disabled-agent",
        },
        headers=HEADERS,
    )

    assert response.status_code == 422
    session = client.app.state.conversation_session
    assert session.scalar(select(func.count()).select_from(Message)) == 0
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 0


def test_missing_team_actor_id_returns_422_without_persisting():
    client = build_client()
    conversation = client.post(
        "/api/conversations", json={"title": "团队协作"}, headers=HEADERS
    ).json()
    response = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "联合研判", "actor_type": "team"},
        headers=HEADERS,
    )

    assert response.status_code == 422
    session = client.app.state.conversation_session
    assert session.scalar(select(func.count()).select_from(Message)) == 0
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 0


def test_sse_honors_last_event_id():
    client = build_client()
    conversation = client.post(
        "/api/conversations", json={"title": "洪水研判"}, headers=HEADERS
    ).json()
    accepted = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "分析洪峰", "actor_type": "agent", "actor_id": "flood"},
        headers=HEADERS,
    ).json()
    response = client.get(
        f"/api/agent-runs/{accepted['run']['id']}/events",
        headers=HEADERS | {"Last-Event-ID": "0"},
    )
    assert "id: 1" in response.text
    assert "event: run.status" in response.text
    assert 'data: {"status":"queued"}' in response.text
