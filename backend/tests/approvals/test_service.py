from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.approvals.service import ApprovalConflictError, ApprovalForbiddenError, ApprovalNotFoundError, ApprovalService
from app.conversations.models import AgentRun, Conversation, Message, ToolInvocation
from app.core.request_context import RequestContext
from app.db.base import Base
from app.audit.models import AuditEvent


def _setup(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'approvals.db'}")
    Base.metadata.create_all(engine)
    return engine


def _fixture(session: Session):
    conversation = Conversation(id="conversation-1", unit_id="unit-1", project_id="project-1", owner_id="requester", title="Approval")
    message = Message(id="message-1", conversation_id=conversation.id, sequence=1, role="user", content="run")
    run = AgentRun(id="run-1", conversation_id=conversation.id, trigger_message_id=message.id, actor_type="agent", actor_id="agent-1", actor_roles_json=["user"], status="running")
    invocation = ToolInvocation(id="invocation-1", run_id=run.id, tool_call_id="call-1", tool_id="water.release", tool_version="1", status="started", arguments_summary={"amount": 10})
    session.add_all([conversation, message, run, invocation])
    session.commit()
    return run, invocation


def _context(user_id="requester", roles=("user",)):
    return RequestContext(user_id=user_id, unit_id="unit-1", project_id="project-1", roles=frozenset(roles))


def test_high_risk_request_is_pending_and_scoped(tmp_path):
    engine = _setup(tmp_path)
    with Session(engine) as session:
        run, invocation = _fixture(session)
        approval = ApprovalService(session, clock=lambda: datetime(2026, 8, 10, tzinfo=timezone.utc)).create_request(
            run=run, invocation=invocation, context=_context(), risk_level="high"
        )
        assert approval.status == "pending"
        assert approval.arguments_digest
        assert ApprovalService(session).list_pending(_context("reviewer", ("project_admin",)))[0].id == approval.id


def test_ordinary_user_cannot_approve(tmp_path):
    engine = _setup(tmp_path)
    with Session(engine) as session:
        run, invocation = _fixture(session)
        approval = ApprovalService(session).create_request(run=run, invocation=invocation, context=_context())
        with pytest.raises(ApprovalForbiddenError):
            ApprovalService(session).approve(approval.id, _context("reviewer", ("user",)))


def test_requester_cannot_approve_own_high_risk_request(tmp_path):
    engine = _setup(tmp_path)
    with Session(engine) as session:
        run, invocation = _fixture(session)
        approval = ApprovalService(session).create_request(run=run, invocation=invocation, context=_context())
        with pytest.raises(ApprovalForbiddenError):
            ApprovalService(session).approve(approval.id, _context("requester", ("project_admin",)))


def test_approval_rejects_changed_arguments_and_duplicate_decision(tmp_path):
    engine = _setup(tmp_path)
    with Session(engine) as session:
        run, invocation = _fixture(session)
        service = ApprovalService(session)
        approval = service.create_request(run=run, invocation=invocation, context=_context())
        invocation.arguments_summary = {"amount": 11}
        with pytest.raises(ApprovalConflictError, match="arguments"):
            service.approve(approval.id, _context("reviewer", ("project_admin",)))
        invocation.arguments_summary = {"amount": 10}
        rejected = service.reject(approval.id, _context("reviewer", ("project_admin",)), reason="not approved")
        assert rejected.status == "rejected"
        with pytest.raises(ApprovalConflictError):
            service.approve(approval.id, _context("reviewer", ("project_admin",)))


def test_expired_request_cannot_be_approved(tmp_path):
    engine = _setup(tmp_path)
    now = datetime(2026, 8, 10, tzinfo=timezone.utc)
    with Session(engine) as session:
        run, invocation = _fixture(session)
        service = ApprovalService(session, clock=lambda: now)
        approval = service.create_request(run=run, invocation=invocation, context=_context(), expires_at=now - timedelta(seconds=1))
        with pytest.raises(ApprovalConflictError, match="expired"):
            service.approve(approval.id, _context("reviewer", ("project_admin",)))


def test_approval_decisions_are_audited(tmp_path):
    engine = _setup(tmp_path)
    with Session(engine) as session:
        run, invocation = _fixture(session)
        service = ApprovalService(session)
        approval = service.create_request(run=run, invocation=invocation, context=_context())
        service.reject(approval.id, _context("reviewer", ("project_admin",)), reason="reviewed")
        session.commit()
        actions = [event.action for event in session.scalars(select(AuditEvent).order_by(AuditEvent.occurred_at, AuditEvent.id))]
        assert actions == ["approval.requested", "approval.rejected"]


def test_approval_history_does_not_leak_other_requesters(tmp_path):
    engine = _setup(tmp_path)
    with Session(engine) as session:
        run, invocation = _fixture(session)
        service = ApprovalService(session)
        approval = service.create_request(run=run, invocation=invocation, context=_context())
        assert service.list_all(_context("other", ("user",)), "all") == []
        with pytest.raises(ApprovalNotFoundError):
            service.get(approval.id, _context("other", ("user",)))
