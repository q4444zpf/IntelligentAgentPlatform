from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.control.models import ControlCommand, ControlCommandApproval, ControlExecutionAttempt, ControlFeedbackRecord, ControlPolicy
from app.control.errors import ControlExecutorUnavailable
from app.control.schemas import ControlFeedbackRequest
from app.control.service import ControlService

from backend.tests.control.test_service import FakeExecutor, _artifact, _context, _policy_request, _propose_request, session


def _counts(engine):
    with Session(engine) as fresh:
        return tuple(fresh.scalar(select(func.count(model.id))) for model in (
            ControlPolicy, ControlCommand, ControlCommandApproval, ControlFeedbackRecord, AuditEvent,
        ))


def test_audit_failure_rolls_back_policy_proposal_approval_and_feedback_mutations(session, monkeypatch):
    service = ControlService(session, executors={"reservoir": FakeExecutor()})
    admin = _context("admin", roles=("project_admin",))
    def fail(*_args, **_kwargs):
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(service.audit, "record", fail)
    with pytest.raises(RuntimeError):
        service.create_policy(admin, _policy_request())
    session.rollback()
    assert _counts(session.bind) == (0, 0, 0, 0, 0)

    monkeypatch.undo()
    policy = service.create_policy(admin, _policy_request())
    from app.control.schemas import ControlPolicyUpdateRequest
    baseline = _counts(session.bind)
    monkeypatch.setattr(service.audit, "record", fail)
    with pytest.raises(RuntimeError):
        service.update_policy(admin, policy.id, ControlPolicyUpdateRequest(enabled=False))
    session.rollback()
    assert _counts(session.bind) == baseline
    with Session(session.bind) as fresh:
        assert fresh.get(ControlPolicy, policy.id).enabled is True
    monkeypatch.undo()
    _artifact(session)
    baseline = _counts(session.bind)
    monkeypatch.setattr(service.audit, "record", fail)
    with pytest.raises(RuntimeError):
        service.propose(_context(), _propose_request(policy.id))
    session.rollback()
    assert _counts(session.bind) == baseline

    monkeypatch.undo()
    command = service.propose(_context(), _propose_request(policy.id))
    baseline = _counts(session.bind)
    monkeypatch.setattr(service.audit, "record", fail)
    with pytest.raises(RuntimeError):
        service.decide(command.id, _context("reviewer", roles=("project_admin",)), "approve")
    session.rollback()
    assert _counts(session.bind) == baseline

    monkeypatch.undo()
    service.decide(command.id, _context("reviewer", roles=("project_admin",)), "approve")
    service.execute(command.id, admin, "execute-1")
    baseline = _counts(session.bind)
    monkeypatch.setattr(service.audit, "record", fail)
    with pytest.raises(RuntimeError):
        service.record_feedback(command.id, _context(), ControlFeedbackRequest(actual_state={"amount": 20}))
    session.rollback()
    assert _counts(session.bind) == baseline


def test_execution_start_result_failure_expiry_and_rollback_pending_are_audited(session):
    executor = FakeExecutor({"outcome": "failed", "external_id": "ext-1"})
    service = ControlService(session, executors={"reservoir": executor})
    admin = _context("admin", roles=("project_admin",))
    _artifact(session)
    policy = service.create_policy(admin, _policy_request())
    command = service.propose(_context(), _propose_request(policy.id))
    service.decide(command.id, _context("reviewer", roles=("project_admin",)), "approve")
    assert service.execute(command.id, admin, "execute-1").status == "failed"
    executor.result = {"outcome": "unknown", "external_id": "ext-2"}
    service.rollback(command.id, admin, "rollback-1")
    with Session(session.bind) as fresh:
        actions = [row.action for row in fresh.scalars(select(AuditEvent).where(AuditEvent.resource_id == command.id).order_by(AuditEvent.occurred_at, AuditEvent.id))]
        assert "control_command.execute_started" in actions
        assert "control_command.execute_completed" in actions
        assert "control_command.rollback_started" in actions
        assert "control_command.rollback_unknown" in actions


def test_completion_audit_failure_leaves_reconcilable_pending_attempt(session, monkeypatch):
    executor = FakeExecutor()
    service = ControlService(session, executors={"reservoir": executor})
    admin = _context("admin", roles=("project_admin",))
    _artifact(session)
    policy = service.create_policy(admin, _policy_request())
    command = service.propose(_context(), _propose_request(policy.id))
    service.decide(command.id, _context("reviewer", roles=("project_admin",)), "approve")
    original = service.audit.record
    def fail_completion(db, request):
        if request.action == "control_command.execute_completed":
            raise RuntimeError("audit completion unavailable")
        return original(db, request)
    monkeypatch.setattr(service.audit, "record", fail_completion)
    with pytest.raises(RuntimeError):
        service.execute(command.id, admin, "execute-1")
    session.rollback()
    with Session(session.bind) as fresh:
        persisted = fresh.get(ControlCommand, command.id)
        attempt = fresh.scalar(select(ControlExecutionAttempt).where(ControlExecutionAttempt.command_id == command.id))
        assert persisted.status == "executing"
        assert attempt.outcome == "pending" and attempt.completed_at is None
        assert fresh.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.action == "control_command.execute_started")) == 1
        assert fresh.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.action == "control_command.execute_completed")) == 0
    monkeypatch.undo()
    with Session(session.bind) as fresh:
        recovered = ControlService(fresh, executors={"reservoir": executor})
        assert recovered.execute(command.id, admin, "execute-1").status == "awaiting_feedback"
    assert len(executor.calls) == 2
    assert executor.calls[0][2] == executor.calls[1][2]


@pytest.mark.parametrize("kind,expected_status", [("execute", "awaiting_feedback"), ("rollback", "rolled_back")])
def test_timeout_reconciliation_audits_unknown_and_completed_once(session, kind, expected_status):
    class TimeoutOnceExecutor(FakeExecutor):
        def __init__(self):
            super().__init__()
            self.seen = set()

        def _call(self, operation, command, external_key):
            self.calls.append((operation, command.id, external_key))
            if (operation, external_key) not in self.seen:
                self.seen.add((operation, external_key))
                raise TimeoutError("response lost")
            return self.result

        def execute(self, command, external_key):
            return self._call("execute", command, external_key)

        def rollback(self, command, _plan, external_key):
            return self._call("rollback", command, external_key)

    executor = TimeoutOnceExecutor()
    service = ControlService(session, executors={"reservoir": executor})
    admin = _context("admin", roles=("project_admin",))
    _artifact(session)
    policy = service.create_policy(admin, _policy_request())
    command = service.propose(_context(), _propose_request(policy.id))
    service.decide(command.id, _context("reviewer", roles=("project_admin",)), "approve")
    if kind == "rollback":
        service.executors["reservoir"] = FakeExecutor({"outcome": "failed"})
        assert service.execute(command.id, admin, "setup-execute").status == "failed"
        service.executors["reservoir"] = executor
    operation = service.execute if kind == "execute" else service.rollback
    with pytest.raises(ControlExecutorUnavailable):
        operation(command.id, admin, "request-1")
    session.close()
    with Session(session.bind) as recovered:
        service = ControlService(recovered, executors={"reservoir": executor})
        operation = service.execute if kind == "execute" else service.rollback
        assert operation(command.id, admin, "request-1").status == expected_status
        assert operation(command.id, admin, "request-1").status == expected_status
        events = recovered.scalars(select(AuditEvent).where(
            AuditEvent.resource_id == command.id,
            AuditEvent.action.in_([f"control_command.{kind}_unknown", f"control_command.{kind}_completed"]),
        )).all()
        assert sorted(event.action for event in events) == [f"control_command.{kind}_completed", f"control_command.{kind}_unknown"]
        assert len({event.idempotency_key for event in events}) == 2
    assert len(executor.calls) == 2
    assert executor.calls[0][2] == executor.calls[1][2]


def test_expiry_and_execution_start_audit_failures_leave_domain_state_unchanged(session, monkeypatch):
    executor = FakeExecutor()
    now = datetime.now(timezone.utc)
    service = ControlService(session, executors={"reservoir": executor}, clock=lambda: now)
    admin = _context("admin", roles=("project_admin",))
    _artifact(session)
    policy = service.create_policy(admin, _policy_request())
    command = service.propose(_context(), _propose_request(policy.id, valid_from=now, valid_until=now + timedelta(minutes=1)))
    service.decide(command.id, _context("reviewer", roles=("project_admin",)), "approve")
    def fail(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(service.audit, "record", fail)
    with pytest.raises(RuntimeError):
        service.execute(command.id, admin, "execute-1")
    session.rollback()
    with Session(session.bind) as fresh:
        assert fresh.get(ControlCommand, command.id).status == "approved"
        assert fresh.scalar(select(func.count(ControlExecutionAttempt.id))) == 0
    assert executor.calls == []

    service.clock = lambda: now + timedelta(minutes=2)
    with pytest.raises(RuntimeError):
        service.execute(command.id, admin, "execute-2")
    session.rollback()
    with Session(session.bind) as fresh:
        assert fresh.get(ControlCommand, command.id).status == "approved"
        assert fresh.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.action == "control_command.expired")) == 0
