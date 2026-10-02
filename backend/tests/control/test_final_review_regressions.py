from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.audit.models import AuditEvent
from app.control.errors import ControlCommandExpired, ControlExecutionError, ControlValidationError
from app.control.models import ControlCommand, ControlExecutionAttempt, ControlFeedbackRecord, ControlPolicy
from app.control.schemas import ControlFeedbackRequest, ControlPolicyUpdateRequest
from app.control.service import ControlService
from tests.control.test_execution_recovery import prepared
from tests.control.test_service import FakeExecutor, _context, _policy_request, session


def test_fresh_deviation_after_success_enables_physical_rollback(session):
    executor = FakeExecutor()
    service, command, admin = prepared(session, executor)
    now = datetime.now(timezone.utc)
    service.clock = lambda: now
    service.execute(command.id, admin, "execute-original")
    assert service.record_feedback(command.id, admin, ControlFeedbackRequest(actual_state={"amount": 20}, observed_at=now)).status == "succeeded"
    observed = now + timedelta(seconds=1)
    response = service.record_feedback(command.id, admin, ControlFeedbackRequest(actual_state={"amount": 40}, observed_at=observed))
    assert response.status == "deviation"
    assert not response.deviation_result["within_threshold"]
    records = session.scalars(select(ControlFeedbackRecord).order_by(ControlFeedbackRecord.observed_at)).all()
    assert [row.actual_state for row in records] == [{"amount": 20}, {"amount": 40}]
    assert service.rollback(command.id, admin, "rollback-original").status == "rolled_back"
    assert [call[0] for call in executor.calls] == ["execute", "rollback"]


def test_fresh_within_threshold_after_deviation_restores_observation_success(session):
    service, command, admin = prepared(session)
    now = datetime.now(timezone.utc)
    service.clock = lambda: now
    service.execute(command.id, admin, "execute-original")
    assert service.record_feedback(command.id, admin, ControlFeedbackRequest(actual_state={"amount": 40}, observed_at=now)).status == "deviation"
    assert service.record_feedback(command.id, admin, ControlFeedbackRequest(actual_state={"amount": 20}, observed_at=now + timedelta(seconds=1))).status == "succeeded"
    with pytest.raises(ControlValidationError):
        service.record_feedback(command.id, admin, ControlFeedbackRequest(actual_state={"amount": 40}, observed_at=now))
    assert len(session.scalars(select(ControlFeedbackRecord)).all()) == 2
    assert session.get(ControlCommand, command.id).status == "succeeded"


INVALID_THRESHOLDS = [True, False, "5", "invalid", -1, float("nan"), float("inf"), float("-inf"), None, [], {}]


@pytest.mark.parametrize("threshold", INVALID_THRESHOLDS)
@pytest.mark.parametrize("operation", ["create", "update"])
def test_policy_rejects_unusable_thresholds_without_persisting(session, threshold, operation):
    service = ControlService(session)
    admin = _context("admin", roles=("project_admin",))
    if operation == "update":
        policy = service.create_policy(admin, _policy_request())
    with pytest.raises(ControlValidationError) as error:
        if operation == "create":
            service.create_policy(admin, _policy_request(deviation_thresholds={"amount": threshold}))
        else:
            service.update_policy(admin, policy.id, ControlPolicyUpdateRequest(deviation_thresholds={"amount": threshold}))
    assert (error.value.status_code, error.value.code) == (422, "control_validation_failed")
    stored = session.scalar(select(ControlPolicy))
    assert stored is None if operation == "create" else stored.deviation_thresholds == {"amount": 5}


@pytest.mark.parametrize("threshold", INVALID_THRESHOLDS)
@pytest.mark.parametrize("operation", ["execute", "feedback"])
def test_invalid_legacy_snapshot_fails_closed_before_transport_or_feedback(session, threshold, operation):
    executor = FakeExecutor()
    service, command, admin = prepared(session, executor)
    if operation == "feedback":
        service.execute(command.id, admin, "execute-original")
    stored = session.get(ControlCommand, command.id)
    stored.deviation_thresholds_snapshot = {"amount": threshold}
    session.commit()
    with pytest.raises(ControlValidationError) as error:
        if operation == "execute":
            service.execute(command.id, admin, "execute-original")
        else:
            service.record_feedback(command.id, admin, ControlFeedbackRequest(actual_state={"amount": 20}))
    assert (error.value.status_code, error.value.code) == (422, "control_validation_failed")
    assert len(executor.calls) == (1 if operation == "feedback" else 0)
    assert session.scalar(select(ControlFeedbackRecord)) is None
    assert stored.status == ("awaiting_feedback" if operation == "feedback" else "approved")
    if operation == "execute":
        assert session.scalar(select(ControlExecutionAttempt)) is None


@pytest.mark.parametrize("threshold", [0, 0.5, 5])
def test_policy_accepts_finite_nonnegative_thresholds(session, threshold):
    policy = ControlService(session).create_policy(_context("admin", roles=("project_admin",)), _policy_request(deviation_thresholds={"amount": threshold}))
    assert policy.deviation_thresholds == {"amount": threshold}


def test_early_execute_preserves_approval_and_later_executes_inside_window(session):
    executor = FakeExecutor()
    service, command, admin = prepared(session, executor)
    now = datetime.now(timezone.utc)
    stored = session.get(ControlCommand, command.id)
    stored.valid_from = now + timedelta(minutes=1)
    stored.valid_until = now + timedelta(minutes=2)
    session.commit()
    service.clock = lambda: now
    with pytest.raises(ControlExecutionError) as error:
        service.execute(command.id, admin, "original")
    assert (error.value.status_code, error.value.code) == (409, "control_invalid_state")
    assert stored.status == "approved"
    assert session.scalar(select(ControlExecutionAttempt)) is None
    assert session.scalar(select(AuditEvent).where(AuditEvent.action == "control_command.expired")) is None
    assert executor.calls == []
    service.clock = lambda: now + timedelta(minutes=1)
    assert service.execute(command.id, admin, "original").status == "awaiting_feedback"
    assert len(executor.calls) == 1


def test_late_execute_still_expires_and_audits_once(session):
    service, command, admin = prepared(session)
    stored = session.get(ControlCommand, command.id)
    service.clock = lambda: stored.valid_until.replace(tzinfo=timezone.utc)
    with pytest.raises(ControlCommandExpired) as error:
        service.execute(command.id, admin, "late")
    assert (error.value.status_code, error.value.code) == (410, "control_command_expired")
    assert stored.status == "expired"
    assert len(session.scalars(select(AuditEvent).where(AuditEvent.action == "control_command.expired")).all()) == 1
