from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.agents.service import BUILTIN_AGENT_ID, AgentNotFoundError
from app.conversations.dispatcher import UnavailableRunDispatcher
from app.conversations.models import AgentRun, Message
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
