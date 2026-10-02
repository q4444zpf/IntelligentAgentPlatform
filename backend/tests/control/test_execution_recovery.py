import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.control import models
from app.control.schemas import ControlFeedbackRequest
from app.control.service import ControlExecutionError, ControlService, ControlValidationError
from tests.control.test_service import (
    FakeExecutor, _artifact, _context, _policy_request, _propose_request, session,
)


def prepared(session, executor=None):
    admin = _context("admin", roles=("project_admin",))
    service = ControlService(session, executors={"reservoir": executor or FakeExecutor()})
    _artifact(session)
    policy = service.create_policy(admin, _policy_request())
    command = service.propose(_context(), _propose_request(policy.id))
    service.decide(command.id, admin, "approve")
    return service, command, admin


class TimeoutExecutor(FakeExecutor):
    def __init__(self):
        super().__init__()
        self.physical_results = {}

    def execute(self, command, external_idempotency_key):
        self.calls.append(("execute", command.id, external_idempotency_key))
        if external_idempotency_key not in self.physical_results:
            self.physical_results[external_idempotency_key] = self.result
            raise TimeoutError("physical operation happened; response was lost")
        return self.physical_results[external_idempotency_key]


def test_execute_commits_pending_attempt_before_external_call(session):
    observed = []

    class ObservingExecutor(FakeExecutor):
        def execute(self, command, external_idempotency_key):
            with Session(session.bind) as observer:
                attempt = observer.scalar(select(models.ControlExecutionAttempt))
                observed.append(None if attempt is None else attempt.outcome)
            return super().execute(command, external_idempotency_key)

    service, command, admin = prepared(session, ObservingExecutor())
    service.execute(command.id, admin, "execute-1")
    assert observed == ["pending"]


def test_execute_reconciles_pending_attempt_with_same_external_key_after_timeout(session):
    executor = TimeoutExecutor()
    service, command, admin = prepared(session, executor)
    with pytest.raises(ControlExecutionError):
        service.execute(command.id, admin, "execute-1")
    session.close()
    with Session(session.bind) as recovered:
        service = ControlService(recovered, executors={"reservoir": executor})
        result = service.execute(command.id, admin, "execute-1")
        assert result.status == "awaiting_feedback"
        attempts = recovered.scalars(select(models.ControlExecutionAttempt)).all()
        assert len(attempts) == 1
        assert attempts[0].outcome == "succeeded"
        assert attempts[0].completed_at is not None
        assert attempts[0].external_id == "ext-1"
    assert len(executor.calls) == 2
    assert executor.calls[0][2] == executor.calls[1][2]
    assert len(executor.physical_results) == 1


def test_execute_rejects_different_key_while_attempt_is_pending(session):
    executor = TimeoutExecutor()
    service, command, admin = prepared(session, executor)
    with pytest.raises(ControlExecutionError):
        service.execute(command.id, admin, "execute-1")
    with pytest.raises(ControlExecutionError) as in_progress:
        service.execute(command.id, admin, "execute-2")
    assert (in_progress.value.status_code, in_progress.value.code) == (409, "control_execution_in_progress")
    assert len(executor.calls) == 1
    attempt = session.scalar(select(models.ControlExecutionAttempt))
    assert attempt.outcome == "unknown"
    assert attempt.completed_at is None


@pytest.mark.parametrize("result", [{"outcome": "invalid"}, {"outcome": "succeeded", "summary": []}, None])
def test_invalid_executor_outcome_is_persisted_as_unknown(session, result):
    executor = FakeExecutor()
    executor.result = result
    service, command, admin = prepared(session, executor)
    response = service.execute(command.id, admin, "execute-1")
    assert response.status == "unknown"
    attempt = session.scalar(select(models.ControlExecutionAttempt))
    assert attempt.outcome == "unknown"
    assert attempt.completed_at is None


def test_completed_adapter_unknown_replays_without_transport_and_feedback_cannot_succeed(session):
    executor = FakeExecutor({"outcome": "unknown"})
    service, command, admin = prepared(session, executor)
    assert service.execute(command.id, admin, "execute-1").status == "unknown"
    assert service.execute(command.id, admin, "execute-1").status == "unknown"
    assert len(executor.calls) == 1
    assert session.scalar(select(models.ControlExecutionAttempt)).completed_at is not None
    assert service.record_feedback(command.id, admin, ControlFeedbackRequest(actual_state={"amount": 20})).status == "unknown"


def test_executor_success_waits_for_feedback_before_terminal_success(session):
    service, command, admin = prepared(session)
    assert service.execute(command.id, admin, "execute-1").status == "awaiting_feedback"
    assert service.record_feedback(command.id, admin, ControlFeedbackRequest(actual_state={"amount": 20})).status == "succeeded"


def test_feedback_persists_observed_at_and_rejects_stale_or_out_of_order_readings(session):
    service, command, admin = prepared(session)
    now = datetime.now(timezone.utc)
    service.clock = lambda: now
    service.execute(command.id, admin, "execute-1")
    for invalid in (now - timedelta(seconds=1), now + timedelta(minutes=6), now.replace(tzinfo=None)):
        with pytest.raises(ControlValidationError):
            service.record_feedback(command.id, admin, ControlFeedbackRequest(actual_state={"amount": 20}, observed_at=invalid))
    first = service.record_feedback(command.id, admin, ControlFeedbackRequest(actual_state={"amount": 20}, observed_at=now))
    assert first.feedback_observed_at.replace(tzinfo=timezone.utc) == now
    with pytest.raises(ControlValidationError):
        service.record_feedback(command.id, admin, ControlFeedbackRequest(actual_state={"amount": 30}, observed_at=now))
    service.record_feedback(command.id, admin, ControlFeedbackRequest(actual_state={"amount": 22}, observed_at=now + timedelta(seconds=1)))
    records = session.scalars(select(models.ControlFeedbackRecord).order_by(models.ControlFeedbackRecord.observed_at)).all()
    assert len(records) == 2
    assert records[0].actual_state == {"amount": 20}
    assert records[0].recorder_id == admin.user_id
    assert records[0].deviation_result == {"deviations": {}, "within_threshold": True}


def test_execute_and_rollback_may_reuse_client_key_because_kind_scopes_it(session):
    executor = FakeExecutor()
    service, command, admin = prepared(session, executor)
    service.execute(command.id, admin, "shared")
    service.record_feedback(command.id, admin, ControlFeedbackRequest(actual_state={"amount": 40}))
    assert service.rollback(command.id, admin, "shared").status == "rolled_back"
    assert [call[0] for call in executor.calls] == ["execute", "rollback"]
    assert len(session.scalars(select(models.ControlExecutionAttempt)).all()) == 2


def test_successful_rollback_replays_with_same_key_without_second_external_call(session):
    executor = FakeExecutor({"outcome": "failed"})
    service, command, admin = prepared(session, executor)
    service.execute(command.id, admin, "execute-1")
    executor.result = {"outcome": "succeeded", "external_id": "rollback-1"}
    service.rollback(command.id, admin, "rollback-1")
    assert service.rollback(command.id, admin, "rollback-1").status == "rolled_back"
    assert len(executor.calls) == 2


def test_policy_threshold_snapshot_not_later_policy_edit_drives_deviation(session):
    service, command, admin = prepared(session)
    policy = session.get(models.ControlPolicy, command.policy_id)
    policy.deviation_thresholds = {"amount": 100}
    session.commit()
    service.execute(command.id, admin, "execute-1")
    response = service.record_feedback(command.id, admin, ControlFeedbackRequest(actual_state={"amount": 30}))
    assert response.status == "deviation"
    assert response.deviation_result["deviations"]["amount"]["threshold"] == 5
    assert len(response.policy_digest) == 64
    assert response.deviation_thresholds_snapshot == {"amount": 5}


def test_migration_backfills_unevaluated_success_and_preserves_evaluated_success():
    import importlib.util
    from pathlib import Path

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    versions = Path(__file__).resolve().parents[2] / "alembic" / "versions"

    def load(name):
        spec = importlib.util.spec_from_file_location(name, versions / name)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        metadata = sa.MetaData()
        from app.identity.models import Menu
        Menu.__table__.to_metadata(metadata)
        sa.Table("collaboration_team_versions", metadata, sa.Column("id", sa.String, primary_key=True), sa.Column("failure_strategy", sa.String))
        sa.Table("artifacts", metadata, sa.Column("id", sa.String, primary_key=True))
        metadata.create_all(connection)
        with Operations.context(MigrationContext.configure(connection)):
            load("20260913_28_control_commands.py").upgrade()
            metadata.reflect(connection)
            connection.execute(metadata.tables["control_policies"].insert().values(
                id="policy", unit_id="unit-1", project_id="project-1", owner_id="operator",
                **_policy_request().model_dump(),
            ))
            now = datetime.now(timezone.utc)
            for command_id, actual_state in (("unevaluated", None), ("evaluated", {"amount": 20})):
                connection.execute(metadata.tables["control_commands"].insert().values(
                    id=command_id, unit_id="unit-1", project_id="project-1", policy_id="policy",
                    requester_id="operator", team_version="version-1", target={}, parameters={},
                    parameter_digest="a" * 64, simulation_artifact_id="artifact", simulation_digest="b" * 64,
                    valid_from=now, valid_until=now + timedelta(minutes=1), required_approvals=1,
                    idempotency_key=command_id, status="succeeded", actual_state=actual_state,
                ))
            load("20260915_31_control_execution_hardening.py").upgrade()
        upgraded = sa.Table("control_commands", sa.MetaData(), autoload_with=connection)
        rows = {row.id: row for row in connection.execute(sa.select(upgraded))}
        assert rows["unevaluated"].status == "awaiting_feedback"
        assert rows["evaluated"].status == "succeeded"
        assert rows["unevaluated"].team_version_id == "version-1"
        assert rows["unevaluated"].deviation_thresholds_snapshot == {"amount": 5}
        assert len(rows["unevaluated"].policy_digest) == 64
        assert rows["unevaluated"].policy_snapshot == _policy_request().model_dump()
        assert rows["unevaluated"].policy_snapshot["enabled"] is True
        assert hashlib.sha256(json.dumps(rows["unevaluated"].policy_snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest() == rows["unevaluated"].policy_digest
        assert connection.scalar(sa.text("SELECT revision FROM control_policies")) == 1
        constraints = sa.inspect(connection).get_unique_constraints("control_execution_attempts")
        assert next(item["column_names"] for item in constraints if item["name"] == "uq_control_execution_request") == ["command_id", "kind", "request_key"]
        assert "control_feedback_records" in sa.inspect(connection).get_table_names()


@pytest.mark.parametrize("actual", ["20", [], {}])
def test_nonnumeric_feedback_cannot_satisfy_a_numeric_threshold(session, actual):
    service, command, admin = prepared(session)
    service.execute(command.id, admin, "execute-1")
    response = service.record_feedback(command.id, admin, ControlFeedbackRequest(actual_state={"amount": actual}))
    assert response.status == "deviation"


def test_late_execute_response_cannot_overwrite_uncertain_rollback(session):
    from app.control.service import ControlExecutorResult

    executor = TimeoutExecutor()
    service, command, admin = prepared(session, executor)
    with pytest.raises(ControlExecutionError):
        service.execute(command.id, admin, "execute-1")
    execute_attempt = session.scalar(select(models.ControlExecutionAttempt))
    executor.result = {"outcome": "unknown"}
    assert service.rollback(command.id, admin, "rollback-1").status == "unknown"
    response = service._complete_attempt(command.id, execute_attempt.id, ControlExecutorResult(outcome="succeeded"))
    assert response.status == "unknown"


def test_pending_attempt_after_process_interruption_reconciles(session):
    class ProcessInterrupted(BaseException):
        pass

    class InterruptedExecutor(TimeoutExecutor):
        def execute(self, command, external_idempotency_key):
            try:
                return super().execute(command, external_idempotency_key)
            except TimeoutError:
                raise ProcessInterrupted()

    executor = InterruptedExecutor()
    service, command, admin = prepared(session, executor)
    with pytest.raises(ProcessInterrupted):
        service.execute(command.id, admin, "execute-1")
    assert session.scalar(select(models.ControlExecutionAttempt)).outcome == "pending"
    assert service.execute(command.id, admin, "execute-1").status == "awaiting_feedback"
    assert len(executor.physical_results) == 1


def test_unresolved_reconciliation_after_window_expires_does_not_call_executor(session):
    executor = TimeoutExecutor()
    service, command, admin = prepared(session, executor)
    with pytest.raises(ControlExecutionError):
        service.execute(command.id, admin, "execute-1")
    service.clock = lambda: command.valid_until.replace(tzinfo=timezone.utc) + timedelta(seconds=1)
    with pytest.raises(ControlValidationError) as expired:
        service.execute(command.id, admin, "execute-1")
    assert (expired.value.status_code, expired.value.code) == (410, "control_command_expired")
    assert len(executor.calls) == 1
    assert session.get(models.ControlCommand, command.id).status == "unknown"


def test_completed_replay_refreshes_attempt_completed_by_another_session(session):
    executor = TimeoutExecutor()
    service, command, admin = prepared(session, executor)
    with pytest.raises(ControlExecutionError):
        service.execute(command.id, admin, "execute-1")
    held_attempt = session.scalar(select(models.ControlExecutionAttempt))
    with Session(session.bind) as other:
        ControlService(other, executors={"reservoir": executor}).execute(command.id, admin, "execute-1")
    assert held_attempt.completed_at is None
    assert service.execute(command.id, admin, "execute-1").status == "awaiting_feedback"
    assert len(executor.calls) == 2


def test_feedback_rejects_observation_before_completion_in_another_session(session):
    executor = TimeoutExecutor()
    service, command, admin = prepared(session, executor)
    started_at = datetime.now(timezone.utc)
    completed_at = started_at + timedelta(seconds=10)
    service.clock = lambda: started_at
    with pytest.raises(ControlExecutionError):
        service.execute(command.id, admin, "execute-1")
    held_attempt = session.scalar(select(models.ControlExecutionAttempt))
    with Session(session.bind) as other:
        recovered = ControlService(other, executors={"reservoir": executor})
        recovered.clock = lambda: completed_at
        assert recovered.execute(command.id, admin, "execute-1").status == "awaiting_feedback"
    assert held_attempt.completed_at is None
    service.clock = lambda: completed_at

    with pytest.raises(ControlValidationError) as stale:
        service.record_feedback(command.id, admin, ControlFeedbackRequest(
            actual_state={"amount": 20}, observed_at=started_at + timedelta(seconds=5),
        ))
    assert (stale.value.status_code, stale.value.code) == (422, "control_validation_failed")

    assert session.get(models.ControlCommand, command.id).status == "awaiting_feedback"
    assert session.scalar(select(models.ControlFeedbackRecord)) is None
    assert service.record_feedback(command.id, admin, ControlFeedbackRequest(
        actual_state={"amount": 20}, observed_at=completed_at,
    )).status == "succeeded"


def test_migration_recovers_legacy_executing_without_attempt_and_downgrades_safely():
    import importlib.util
    from pathlib import Path

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    versions = Path(__file__).resolve().parents[2] / "alembic" / "versions"

    def load(name):
        spec = importlib.util.spec_from_file_location(name, versions / name)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        metadata = sa.MetaData()
        from app.identity.models import Menu
        Menu.__table__.to_metadata(metadata)
        sa.Table("collaboration_team_versions", metadata, sa.Column("id", sa.String, primary_key=True), sa.Column("failure_strategy", sa.String))
        sa.Table("artifacts", metadata, sa.Column("id", sa.String, primary_key=True))
        metadata.create_all(connection)
        with Operations.context(MigrationContext.configure(connection)):
            # Revision 30 only merges heads; its control schema is created by 28.
            load("20260913_28_control_commands.py").upgrade()
            load("20260914_30_merge_control_skill_heads.py").upgrade()
            metadata.reflect(connection)
            commands = metadata.tables["control_commands"]
            attempts = metadata.tables["control_execution_attempts"]
            approvals = metadata.tables["control_command_approvals"]
            connection.execute(metadata.tables["control_policies"].insert().values(
                id="policy", unit_id="unit-1", project_id="project-1", owner_id="operator",
                **_policy_request().model_dump(),
            ))
            now = datetime.now(timezone.utc)
            for command_id in ("orphaned", "tracked"):
                connection.execute(commands.insert().values(
                    id=command_id, unit_id="unit-1", project_id="project-1", policy_id="policy",
                    requester_id="operator", team_version="version-1", target={"id": "reservoir-1"},
                    parameters={"amount": 20}, parameter_digest="a" * 64,
                    simulation_artifact_id="artifact", simulation_digest="b" * 64,
                    valid_from=now, valid_until=now + timedelta(minutes=1), required_approvals=1,
                    rollback_plan={"amount": 10}, expected_state={"amount": 20},
                    idempotency_key=command_id, status="executing",
                ))
            connection.execute(attempts.insert().values(
                id="attempt", command_id="tracked", kind="execute", request_key="execute-1",
                external_idempotency_key="existing-external-key", outcome="pending",
            ))
            connection.execute(approvals.insert().values(
                id="approval", command_id="orphaned", approver_id="admin", decision="approve",
            ))
            original_commands = {row["id"]: dict(row) for row in connection.execute(sa.select(commands)).mappings()}
            original_attempt = dict(connection.execute(sa.select(attempts)).mappings().one())
            original_approval = dict(connection.execute(sa.select(approvals)).mappings().one())
            migration = load("20260915_31_control_execution_hardening.py")
            assert migration.down_revision == "20260914_30"
            migration.upgrade()

            upgraded = sa.Table("control_commands", sa.MetaData(), autoload_with=connection)
            rows = {row.id: row for row in connection.execute(sa.select(upgraded))}
            assert rows["orphaned"].status == "unknown"
            assert rows["tracked"].status == "executing"
            assert rows["orphaned"].external_command_ref is None
            assert connection.execute(sa.select(attempts)).mappings().all() == [original_attempt]
            assert connection.execute(sa.select(approvals)).mappings().all() == [original_approval]
            assert connection.scalar(sa.text("SELECT COUNT(*) FROM control_feedback_records")) == 0

            migration.downgrade()
            original_commands["orphaned"]["status"] = "unknown"
            assert {row["id"]: dict(row) for row in connection.execute(sa.select(commands)).mappings()} == original_commands
            assert connection.execute(sa.select(attempts)).mappings().all() == [original_attempt]
            assert connection.execute(sa.select(approvals)).mappings().all() == [original_approval]
            assert "control_feedback_records" not in sa.inspect(connection).get_table_names()
    engine.dispose()


def test_control_migration_rejects_invalid_legacy_strategy_before_schema_changes():
    import importlib.util
    from pathlib import Path
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).resolve().parents[2] / "alembic/versions/20260913_28_control_commands.py"
    spec = importlib.util.spec_from_file_location("control_migration_preflight", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE collaboration_team_versions (id TEXT PRIMARY KEY, failure_strategy TEXT)"))
        connection.execute(sa.text("INSERT INTO collaboration_team_versions VALUES ('legacy', 'ignore')"))
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="1 invalid") as error:
                migration.upgrade()
        assert "SELECT id, failure_strategy FROM collaboration_team_versions WHERE failure_strategy NOT IN ('fail_fast','continue_then_synthesize')" in str(error.value)
        assert "control_policies" not in sa.inspect(connection).get_table_names()
        assert connection.scalar(sa.text("SELECT failure_strategy FROM collaboration_team_versions")) == "ignore"
    engine.dispose()
