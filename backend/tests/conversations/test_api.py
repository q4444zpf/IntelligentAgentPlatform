from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.conversations.dispatcher import UnavailableRunDispatcher
from app.conversations.repository import ConversationRepository
from app.conversations.router import create_router
from app.conversations.service import ConversationService
from app.db.base import Base


def build_client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    service = ConversationService(
        ConversationRepository(session), UnavailableRunDispatcher()
    )
    app = FastAPI()
    app.state.allow_dev_identity = True
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
