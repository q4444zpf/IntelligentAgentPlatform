from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.approvals.models import Approval
from app.approvals.router import router
from app.approvals.service import arguments_digest
from app.conversations.models import AgentRun, Conversation, Message, RunEvent, ToolInvocation
from app.conversations.router import default_run_dispatcher
from app.core.database import get_session
from app.db.base import Base


HEADERS = {"X-Unit-ID": "unit-1", "X-User-ID": "requester", "X-Project-ID": "project-1"}
ADMIN_HEADERS = {**HEADERS, "X-User-ID": "reviewer", "X-User-Roles": "project_admin"}


def build_client():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(engine)
    conversation = Conversation(id="conversation-1", unit_id="unit-1", project_id="project-1", owner_id="requester", title="approval")
    message = Message(id="message-1", conversation_id=conversation.id, sequence=1, role="user", content="run")
    run = AgentRun(id="run-1", conversation_id=conversation.id, trigger_message_id=message.id, actor_type="agent", actor_id="agent-1", actor_roles_json=["user"], status="waiting_approval")
    invocation = ToolInvocation(id="invocation-1", run_id=run.id, tool_call_id="call-1", tool_id="water.release", tool_version="1", status="waiting_approval", arguments_summary={"amount": 10})
    approval = Approval(id="approval-1", run_id=run.id, invocation_id=invocation.id, tool_id=invocation.tool_id, tool_version="1", unit_id="unit-1", project_id="project-1", requester_id="requester", requester_roles=["user"], assignee_role="project_admin", risk_level="high", arguments_summary={"amount": 10}, arguments_digest=arguments_digest({"amount": 10}), status="pending", expires_at=datetime(2026, 8, 11, tzinfo=timezone.utc))
    session.add_all([conversation, message, run, invocation, approval])
    session.commit()
    app = FastAPI()
    app.state.allow_dev_identity = True
    app.dependency_overrides[get_session] = lambda: session
    app.include_router(router, prefix="/api/approvals")
    return TestClient(app), session


def test_approval_list_is_scoped_and_returns_pending_items():
    client, _ = build_client()
    response = client.get("/api/approvals", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert response.json()[0]["id"] == "approval-1"
    assert client.get("/api/approvals", headers=HEADERS).json() == []


def test_approval_can_be_rejected_by_assigned_admin():
    client, session = build_client()
    response = client.post("/api/approvals/approval-1/reject", json={"reason": "risk"}, headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert session.get(Approval, "approval-1").decision_reason == "risk"


def test_requester_cannot_approve_own_request():
    client, _ = build_client()
    response = client.post("/api/approvals/approval-1/approve", json={}, headers={**HEADERS, "X-User-Roles": "project_admin"})
    assert response.status_code == 403


def test_approve_queues_run_and_dispatches_resume(monkeypatch):
    client, session = build_client()
    resumed: list[str] = []
    monkeypatch.setattr(default_run_dispatcher, "resume_approval", resumed.append)
    response = client.post("/api/approvals/approval-1/approve", json={"reason": "verified"}, headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert session.get(AgentRun, "run-1").status == "queued"
    assert resumed == ["approval-1"]
    events = session.query(RunEvent).filter_by(run_id="run-1").order_by(RunEvent.sequence).all()
    assert [event.event_type for event in events] == ["approval.resolved", "run.status"]
