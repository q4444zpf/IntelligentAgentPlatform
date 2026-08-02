from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.conversations.dispatcher import UnavailableRunDispatcher
from app.conversations.models import ToolInvocation
from app.conversations.repository import ConversationRepository
from app.conversations.router import create_router as create_conversation_router
from app.conversations.service import ConversationService
from app.db.base import Base
from app.tools.router import create_router
from app.tools.service import ToolService
from app.tools.store import ToolStore


AUTH_HEADERS = {"X-User-ID": "u1", "X-Project-ID": "p1"}
ADMIN_HEADERS = {**AUTH_HEADERS, "X-User-Role": "admin"}


@pytest.fixture
def client(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'tools-api.db'}")
    Base.metadata.create_all(engine)
    service = ToolService(
        ToolStore(sessionmaker(bind=engine, expire_on_commit=False, class_=Session))
    )
    app = FastAPI()
    app.state.allow_dev_identity = True
    app.include_router(create_router(service), prefix="/api/tools")
    with TestClient(app) as test_client:
        yield test_client


def test_initializes_two_builtin_tools(client):
    response = client.get("/api/tools", headers=AUTH_HEADERS)

    assert response.status_code == 200
    tools = response.json()
    assert [item["tool_id"] for item in tools] == [
        "system.get_current_time",
        "system.get_runtime_context",
    ]
    assert all(item["source"] == "builtin" for item in tools)
    assert all(item["version"] == "1.0.0" for item in tools)
    assert all(item["published"] is True for item in tools)


def test_reads_toggles_and_returns_not_found(client):
    read = client.get("/api/tools/system.get_current_time", headers=AUTH_HEADERS)
    assert read.status_code == 200
    assert read.json()["enabled"] is True

    toggled = client.patch("/api/tools/system.get_current_time/toggle", headers=ADMIN_HEADERS)
    assert toggled.status_code == 200
    assert toggled.json()["enabled"] is False

    missing = client.get("/api/tools/system.missing", headers=AUTH_HEADERS)
    assert missing.status_code == 404


def test_invalid_tool_id_returns_unprocessable_entity(client):
    response = client.get("/api/tools/invalid$id", headers=AUTH_HEADERS)
    assert response.status_code == 422


def test_tool_registry_requires_authentication(client):
    assert client.get("/api/tools").status_code == 401
    assert client.patch("/api/tools/system.get_current_time/toggle").status_code == 401


def test_regular_user_cannot_toggle_tool(client):
    response = client.patch(
        "/api/tools/system.get_current_time/toggle", headers=AUTH_HEADERS
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Administrator permission is required"}


def test_authenticated_regular_user_can_read_tools(client):
    assert client.get("/api/tools", headers=AUTH_HEADERS).status_code == 200
    assert client.get(
        "/api/tools/system.get_current_time", headers=AUTH_HEADERS
    ).status_code == 200

def build_invocation_client(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'invocations-api.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    service = ConversationService(
        ConversationRepository(session), UnavailableRunDispatcher()
    )
    app = FastAPI()
    app.state.allow_dev_identity = True
    app.state.conversation_session = session
    app.include_router(
        create_conversation_router(lambda _session: service), prefix="/api"
    )
    return TestClient(app), session


def create_run(client, headers):
    conversation = client.post(
        "/api/conversations", json={"title": "工具审计"}, headers=headers
    ).json()
    accepted = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "现在几点", "actor_type": "agent"},
        headers=headers,
    ).json()
    return accepted["run"]["id"]


def test_lists_scoped_tool_invocations_in_creation_order(tmp_path):
    client, session = build_invocation_client(tmp_path)
    headers = {"X-User-ID": "u1", "X-Project-ID": "p1"}
    run_id = create_run(client, headers)
    session.add_all(
        [
            ToolInvocation(
                id="invocation-2", run_id=run_id, tool_call_id="call-2",
                tool_id="system.get_runtime_context", tool_version="1.0.0",
                status="completed", arguments_summary={"fields": ["timezone"]},
                result_summary={"keys": ["timezone"]}, duration_ms=9,
                created_at=datetime(2026, 8, 2, tzinfo=UTC),
            ),
            ToolInvocation(
                id="invocation-1", run_id=run_id, tool_call_id="call-1",
                tool_id="system.get_current_time", tool_version="1.0.0",
                status="failed", arguments_summary={"keys": []},
                result_summary=None, duration_ms=3, error_code="TOOL_FAILED",
                created_at=datetime(2026, 8, 2, tzinfo=UTC) + timedelta(seconds=1),
            ),
        ]
    )
    session.commit()

    response = client.get(f"/api/agent-runs/{run_id}/tool-invocations", headers=headers)

    assert response.status_code == 200
    items = response.json()
    assert [item["id"] for item in items] == ["invocation-2", "invocation-1"]
    assert items[0]["arguments_summary"] == {"fields": ["timezone"]}
    assert items[0]["result_summary"] == {"keys": ["timezone"]}
    assert items[0]["duration_ms"] == 9
    assert "arguments" not in items[0]
    assert "result" not in items[0]


@pytest.mark.parametrize("headers", [
    {"X-User-ID": "other", "X-Project-ID": "p1"},
    {"X-User-ID": "u1", "X-Project-ID": "other"},
])
def test_tool_invocations_hide_runs_outside_request_scope(tmp_path, headers):
    client, _session = build_invocation_client(tmp_path)
    run_id = create_run(client, {"X-User-ID": "u1", "X-Project-ID": "p1"})

    response = client.get(f"/api/agent-runs/{run_id}/tool-invocations", headers=headers)

    assert response.status_code == 404
    assert response.json() == {"detail": "Resource was not found"}


def test_tool_invocations_return_not_found_for_missing_run(tmp_path):
    client, _session = build_invocation_client(tmp_path)

    response = client.get(
        "/api/agent-runs/missing/tool-invocations",
        headers={"X-User-ID": "u1", "X-Project-ID": "p1"},
    )

    assert response.status_code == 404
