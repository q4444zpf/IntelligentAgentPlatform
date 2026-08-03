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


AUTH_HEADERS = {"X-Unit-ID": "unit-1", "X-User-ID": "u1", "X-Project-ID": "p1"}
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
    app.state.tool_service = service
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
        "/api/tools/system.get_current_time/toggle",
        headers={**AUTH_HEADERS, "X-Request-ID": "tool-denied-1"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Administrator permission is required"}
    from sqlalchemy import select
    from app.audit.models import AuditEvent
    with client.app.state.tool_service.store.session_factory() as session:
        event = session.scalar(select(AuditEvent))
    assert event.source == "tool"
    assert event.status == "failed"
    assert event.error_code == "PERMISSION_DENIED"
    assert event.resource_id == "system.get_current_time"
    assert event.metadata_json == {}
    assert event.trace_id == "tool-denied-1"


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

def test_missing_tool_toggle_records_failed_audit(client):
    from sqlalchemy import select
    from app.audit.models import AuditEvent

    response = client.patch(
        "/api/tools/system.missing/toggle",
        headers={**ADMIN_HEADERS, "X-Request-ID": "tool-missing-1"},
    )
    assert response.status_code == 404
    with client.app.state.tool_service.store.session_factory() as session:
        event = session.scalar(select(AuditEvent))
    assert event.source == "tool"
    assert event.status == "failed"
    assert event.error_code == "TOOL_NOT_FOUND"
    assert event.resource_id == "system.missing"
    assert event.metadata_json == {}
    assert event.trace_id == "tool-missing-1"


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
    headers = AUTH_HEADERS
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
    {**AUTH_HEADERS, "X-User-ID": "other"},
    {**AUTH_HEADERS, "X-Project-ID": "other"},
])
def test_tool_invocations_hide_runs_outside_request_scope(tmp_path, headers):
    client, _session = build_invocation_client(tmp_path)
    run_id = create_run(client, AUTH_HEADERS)

    response = client.get(f"/api/agent-runs/{run_id}/tool-invocations", headers=headers)

    assert response.status_code == 404
    assert response.json() == {"detail": "Resource was not found"}


def test_tool_invocations_return_not_found_for_missing_run(tmp_path):
    client, _session = build_invocation_client(tmp_path)

    response = client.get(
        "/api/agent-runs/missing/tool-invocations",
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 404

def test_toggle_commits_tool_and_management_audit_together(tmp_path):
    from sqlalchemy import select
    from app.audit.models import AuditEvent
    from app.core.request_context import RequestContext

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'tool-audit.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    service = ToolService(ToolStore(factory))
    context = RequestContext(unit_id="unit-1", project_id="p1", user_id="u1")
    with factory() as session:
        result = service.toggle("system.get_current_time", context=context, session=session, request_id="tool-toggle-1")
        event = session.scalar(select(AuditEvent))
    assert result.enabled is False
    assert event.action == "resource.disabled"
    assert event.source == "tool"
    assert event.resource_id == "system.get_current_time"


def test_toggle_rolls_back_when_audit_recorder_fails(tmp_path):
    from app.audit.recorder import AuditRecorder
    from app.core.request_context import RequestContext

    class FailingRecorder(AuditRecorder):
        def record(self, session, request):
            raise RuntimeError("audit unavailable")

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'tool-audit-fail.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    service = ToolService(ToolStore(factory), audit_recorder=FailingRecorder())
    context = RequestContext(unit_id="unit-1", project_id="p1", user_id="u1")
    with factory() as session, pytest.raises(RuntimeError, match="audit unavailable"):
        service.toggle("system.get_current_time", context=context, session=session, request_id="tool-toggle-rollback")
    assert ToolService(ToolStore(factory)).get("system.get_current_time").enabled is True
