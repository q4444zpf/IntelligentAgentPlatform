import time
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event, Thread, local
from unittest.mock import ANY

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.audit.models import AuditEvent
from app.conversations.models import AgentRun, Conversation, Message, RunEvent
from app.conversations.repository import ConversationRepository
from app.db.base import Base
from app.runtime.run_lifecycle import SandboxRunCoordinator
from app.runtime.workflow_runner import RunnerUnavailableError


class SequenceRunner:
    def __init__(self, statuses, *, submit_error=None, status_error=None, cleanup_error=None, status_callback=None):
        self.statuses = list(statuses)
        self.submit_error = submit_error
        self.status_error = status_error
        self.cleanup_error = cleanup_error
        self.status_callback = status_callback
        self.calls = []

    def submit(
        self,
        run_id,
        agent_version,
        checkpoint_key,
        *,
        monotonic_deadline=None,
        **execution,
    ):
        self.calls.append(("submit", run_id, agent_version, checkpoint_key, execution))
        if self.submit_error:
            raise self.submit_error
        return {"run_id": run_id, "status": "accepted"}

    def status(self, run_id, *, monotonic_deadline=None):
        self.calls.append(("status", run_id))
        if self.status_error:
            raise self.status_error
        if self.status_callback:
            self.status_callback(run_id)
        return self.statuses.pop(0)

    def terminate(self, run_id, *, monotonic_deadline=None):
        self.calls.append(("terminate", run_id))
        return {"run_id": run_id, "status": "terminated"}

    def cleanup(self, run_id, *, monotonic_deadline=None):
        self.calls.append(("cleanup", run_id))
        if self.cleanup_error:
            raise self.cleanup_error
        return {"run_id": run_id, "status": "cleaned"}


class FakeSnapshots:
    def __init__(self, *, actor=None, created_at=None):
        self.created = []
        self.actor = actor or SimpleNamespace(kind="agent")
        self.created_at = created_at or datetime.now(UTC)

    def _snapshot(self, run_id):
        return SimpleNamespace(
            snapshot_id="snapshot-1",
            run_id=run_id,
            digest="a" * 64,
            expires_at=None,
            payload=SimpleNamespace(
                unit_id="unit-1",
                project_id="project-1",
                actor=self.actor,
            ),
            created_at=self.created_at,
        )

    def create(self, run_id):
        snapshot = self._snapshot(run_id)
        self.created.append(snapshot)
        return snapshot

    def get_for_run(self, run_id):
        return self._snapshot(run_id)


class FakeTokens:
    def __init__(self):
        self.issued = []
        self.revoked = []

    def issue(self, snapshot, actions, deadline_at):
        issued = SimpleNamespace(value="run-secret-token", claims=SimpleNamespace())
        self.issued.append(SimpleNamespace(
            value=issued.value,
            snapshot=snapshot,
            actions=set(actions),
            deadline_at=deadline_at,
        ))
        return issued

    def revoke(self, run_id, reason):
        if not any(item[0] == run_id for item in self.revoked):
            self.revoked.append((run_id, reason))


def make_coordinator(factory, runner, **kwargs):
    snapshots = kwargs.pop("snapshots", FakeSnapshots())
    tokens = kwargs.pop("tokens", FakeTokens())
    coordinator = SandboxRunCoordinator(
        factory,
        runner,
        snapshot_service_factory=lambda _session: snapshots,
        token_service_factory=lambda _session: tokens,
        gateway_url="http://api:8000/internal/runner",
        **kwargs,
    )
    return coordinator


@pytest.fixture
def run_factory(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'lifecycle.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with factory.begin() as session:
        conversation = Conversation(unit_id="unit-1", project_id="project-1", owner_id="user-1", title="sandbox")
        session.add(conversation)
        session.flush()
        message = Message(conversation_id=conversation.id, role="user", content="run")
        session.add(message)
        session.flush()
        run = AgentRun(
            conversation_id=conversation.id,
            trigger_message_id=message.id,
            actor_type="agent",
            actor_id="agent-v1",
            actor_roles_json=["user"],
            status="queued",
        )
        session.add(run)
        session.flush()
        session.add(RunEvent(run_id=run.id, sequence=1, event_type="run.status", payload={"status": "queued"}))
        run_id = run.id
    return factory, run_id


def snapshot(factory, run_id):
    with factory() as session:
        run = session.get(AgentRun, run_id)
        events = list(session.scalars(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.sequence)))
        audits = list(session.scalars(select(AuditEvent).where(AuditEvent.run_id == run_id).order_by(AuditEvent.occurred_at)))
        return SimpleNamespace(run=run, events=events, audits=audits)


def test_coordinator_persists_completed_terminal_state_and_cleanup(run_factory):
    factory, run_id = run_factory
    runner = SequenceRunner([{"status": "running"}, {"status": "exited", "exit_code": 0, "oom_killed": False}])

    make_coordinator(factory, runner, poll_interval=0, timeout_seconds=5).execute(run_id)

    state = snapshot(factory, run_id)
    assert state.run.status == "completed"
    assert [event.event_type for event in state.events] == [
        "run.status", "sandbox.started", "run.status", "sandbox.finished", "run.status", "sandbox.cleanup",
    ]
    assert runner.calls[-1] == ("cleanup", run_id)
    assert [audit.action for audit in state.audits] == ["sandbox.run.started", "sandbox.run.completed"]


@pytest.mark.parametrize(
    ("status_payload", "expected_status", "expected_code"),
    [
        ({"status": "exited", "exit_code": 2, "oom_killed": False}, "failed", "sandbox_failed"),
        ({"status": "exited", "exit_code": 137, "oom_killed": True}, "failed", "sandbox_oom"),
        ({"status": "terminated"}, "cancelled", "sandbox_cancelled"),
    ],
)
def test_coordinator_maps_container_terminal_states(run_factory, status_payload, expected_status, expected_code):
    factory, run_id = run_factory
    make_coordinator(factory, SequenceRunner([status_payload]), poll_interval=0).execute(run_id)

    state = snapshot(factory, run_id)
    assert state.run.status == expected_status
    error = next(event for event in state.events if event.event_type == "run.error")
    assert error.payload["code"] == expected_code
    assert "/" not in error.payload["message"]


def test_coordinator_times_out_terminates_and_cleans_up(run_factory):
    factory, run_id = run_factory
    runner = SequenceRunner([{"status": "running"}] * 3)
    ticks = iter([0.0, 0.5, 1.1])

    make_coordinator(
        factory,
        runner,
        poll_interval=0,
        timeout_seconds=1,
        monotonic=lambda: next(ticks),
    ).execute(run_id)

    state = snapshot(factory, run_id)
    assert state.run.status == "failed"
    assert next(event for event in state.events if event.event_type == "run.error").payload["code"] == "sandbox_timeout"
    assert ("terminate", run_id) in runner.calls
    assert runner.calls[-1] == ("cleanup", run_id)


def test_timeout_state_is_persisted_before_container_termination(run_factory):
    factory, run_id = run_factory

    class RacingRunner(SequenceRunner):
        def terminate(self, current_run_id, *, monotonic_deadline=None):
            with factory.begin() as session:
                run = session.get(AgentRun, current_run_id)
                if run.status == "running":
                    run.status = "completed"
            return super().terminate(
                current_run_id,
                monotonic_deadline=monotonic_deadline,
            )

    runner = RacingRunner([{"status": "running"}] * 3)
    ticks = iter([0.0, 0.5, 1.1])

    make_coordinator(
        factory,
        runner,
        poll_interval=0,
        timeout_seconds=1,
        monotonic=lambda: next(ticks),
    ).execute(run_id)

    state = snapshot(factory, run_id)
    assert state.run.status == "failed"
    assert next(
        event for event in state.events if event.event_type == "run.error"
    ).payload["code"] == "sandbox_timeout"


def test_team_watchdog_uses_snapshot_deadline_before_worker_snapshot_discovery(
    run_factory,
):
    factory, run_id = run_factory
    base = datetime(2026, 9, 4, 8, 0, tzinfo=UTC)
    snapshots = FakeSnapshots(
        actor=SimpleNamespace(kind="team", timeout_seconds=1),
        created_at=base,
    )
    tokens = FakeTokens()
    runner = SequenceRunner([{"status": "running"}] * 3)
    ticks = iter([0.0, 0.5, 1.1])

    make_coordinator(
        factory,
        runner,
        snapshots=snapshots,
        tokens=tokens,
        poll_interval=0,
        timeout_seconds=5,
        monotonic=lambda: next(ticks),
        clock=lambda: base,
    ).execute(run_id)

    submission = next(call for call in runner.calls if call[0] == "submit")[4]
    assert datetime.fromisoformat(submission["execution_deadline_at"]) == (
        base + timedelta(seconds=1)
    )
    assert tokens.issued[0].deadline_at == base + timedelta(seconds=5)
    assert snapshot(factory, run_id).run.status == "failed"
    assert ("terminate", run_id) in runner.calls


def test_team_watchdog_terminates_worker_blocked_on_initial_snapshot(run_factory):
    from app.runtime.execution_contract import RunExecutionRequest
    from app.runtime.runner_gateway_client import RunnerGatewayBusinessError
    from app.runtime.sandbox_runtime import SandboxRuntime

    factory, run_id = run_factory
    snapshots = FakeSnapshots(
        actor=SimpleNamespace(kind="team", timeout_seconds=1),
    )
    snapshot_entered = Event()
    snapshot_returned = Event()
    release_snapshot = Event()

    class BlockingGateway:
        def get_snapshot(self):
            snapshot_entered.set()
            assert release_snapshot.wait(timeout=5)
            snapshot_returned.set()
            raise RunnerGatewayBusinessError("runner_gateway_unavailable")

        def complete(self, _request, _idempotency_key):
            return {}

    class BlockingSnapshotRunner:
        def __init__(self):
            self.calls = []
            self.worker = None

        def submit(
            self,
            current_run_id,
            agent_version,
            checkpoint_key,
            *,
            monotonic_deadline=None,
            **execution,
        ):
            self.calls.append(
                (
                    "submit",
                    current_run_id,
                    agent_version,
                    checkpoint_key,
                    execution,
                )
            )
            request = RunExecutionRequest(
                run_id=current_run_id,
                agent_version=agent_version,
                checkpoint_key=checkpoint_key,
                **execution,
            )
            self.worker = Thread(
                target=lambda: SandboxRuntime(BlockingGateway()).execute(request),
                daemon=True,
            )
            self.worker.start()
            assert snapshot_entered.wait(timeout=2)
            return {"run_id": current_run_id, "status": "accepted"}

        def status(self, current_run_id, *, monotonic_deadline=None):
            self.calls.append(("status", current_run_id))
            return {"run_id": current_run_id, "status": "running"}

        def terminate(self, current_run_id, *, monotonic_deadline=None):
            self.calls.append(("terminate", current_run_id))
            assert not snapshot_returned.is_set()
            release_snapshot.set()
            return {"run_id": current_run_id, "status": "terminated"}

        def cleanup(self, current_run_id, *, monotonic_deadline=None):
            self.calls.append(("cleanup", current_run_id))
            release_snapshot.set()
            if self.worker is not None:
                self.worker.join(timeout=2)
            return {"run_id": current_run_id, "status": "cleaned"}

    runner = BlockingSnapshotRunner()

    make_coordinator(
        factory,
        runner,
        snapshots=snapshots,
        poll_interval=0.01,
        timeout_seconds=5,
    ).execute(run_id)

    state = snapshot(factory, run_id)
    assert state.run.status == "failed"
    assert next(
        event for event in state.events if event.event_type == "run.error"
    ).payload["code"] == "sandbox_timeout"
    assert ("terminate", run_id) in runner.calls


def test_team_watchdog_expires_while_runner_submit_is_blocked(run_factory):
    factory, run_id = run_factory

    class BlockingSubmitRunner(SequenceRunner):
        def submit(
            self,
            current_run_id,
            agent_version,
            checkpoint_key,
            *,
            monotonic_deadline=None,
            **execution,
        ):
            self.calls.append(
                (
                    "submit",
                    current_run_id,
                    agent_version,
                    checkpoint_key,
                    execution,
                )
            )
            timeout = (
                0.6
                if monotonic_deadline is None
                else max(0, monotonic_deadline - time.monotonic())
            )
            Event().wait(timeout=timeout)
            if monotonic_deadline is not None:
                raise RunnerUnavailableError("runner deadline expired")
            return {"run_id": current_run_id, "status": "accepted"}

    runner = BlockingSubmitRunner([{"status": "running"}])
    started_at = time.monotonic()

    make_coordinator(
        factory,
        runner,
        poll_interval=0,
        timeout_seconds=0.15,
    ).execute(run_id)

    assert time.monotonic() - started_at < 0.45
    state = snapshot(factory, run_id)
    assert state.run.status == "failed"
    assert next(
        event for event in state.events if event.event_type == "run.error"
    ).payload["code"] == "sandbox_timeout"


def test_team_watchdog_expires_while_runner_status_is_blocked(run_factory):
    factory, run_id = run_factory

    class BlockingStatusRunner(SequenceRunner):
        def status(self, current_run_id, *, monotonic_deadline=None):
            self.calls.append(("status", current_run_id))
            timeout = (
                0.6
                if monotonic_deadline is None
                else max(0, monotonic_deadline - time.monotonic())
            )
            Event().wait(timeout=timeout)
            if monotonic_deadline is not None:
                raise RunnerUnavailableError("runner deadline expired")
            return {"run_id": current_run_id, "status": "running"}

    runner = BlockingStatusRunner([])
    started_at = time.monotonic()

    make_coordinator(
        factory,
        runner,
        poll_interval=0,
        timeout_seconds=0.15,
    ).execute(run_id)

    assert time.monotonic() - started_at < 0.45
    assert snapshot(factory, run_id).run.status == "failed"


def test_team_watchdog_expires_while_recovery_status_is_blocked(run_factory):
    factory, run_id = run_factory
    with factory.begin() as session:
        session.get(AgentRun, run_id).status = "running"

    class BlockingStatusRunner(SequenceRunner):
        def status(self, current_run_id, *, monotonic_deadline=None):
            self.calls.append(("status", current_run_id))
            timeout = (
                0.6
                if monotonic_deadline is None
                else max(0, monotonic_deadline - time.monotonic())
            )
            Event().wait(timeout=timeout)
            if monotonic_deadline is not None:
                raise RunnerUnavailableError("runner deadline expired")
            return {"run_id": current_run_id, "status": "running"}

    runner = BlockingStatusRunner([])
    started_at = time.monotonic()

    make_coordinator(
        factory,
        runner,
        poll_interval=0,
        timeout_seconds=0.15,
    ).recover(run_id)

    assert time.monotonic() - started_at < 0.45
    assert snapshot(factory, run_id).run.status == "failed"


def test_runner_failure_after_team_deadline_remains_sandbox_timeout(run_factory):
    factory, run_id = run_factory

    class LateFailingStatusRunner(SequenceRunner):
        def status(self, current_run_id, *, monotonic_deadline=None):
            self.calls.append(("status", current_run_id))
            timeout = 0.6
            if monotonic_deadline is not None:
                timeout = max(
                    0,
                    monotonic_deadline - time.monotonic() + 0.05,
                )
            Event().wait(timeout=timeout)
            raise RunnerUnavailableError("late runner failure")

    runner = LateFailingStatusRunner([])

    make_coordinator(
        factory,
        runner,
        poll_interval=0,
        timeout_seconds=0.15,
    ).execute(run_id)

    state = snapshot(factory, run_id)
    assert state.run.status == "failed"
    assert next(
        event for event in state.events if event.event_type == "run.error"
    ).payload["code"] == "sandbox_timeout"


def test_terminal_success_returned_after_team_deadline_remains_timeout(
    run_factory,
):
    factory, run_id = run_factory

    class LateTerminalRunner(SequenceRunner):
        def status(self, current_run_id, *, monotonic_deadline=None):
            self.calls.append(("status", current_run_id))
            timeout = 0.6
            if monotonic_deadline is not None:
                timeout = max(
                    0,
                    monotonic_deadline - time.monotonic() + 0.05,
                )
            Event().wait(timeout=timeout)
            return {
                "run_id": current_run_id,
                "status": "exited",
                "exit_code": 0,
                "oom_killed": False,
            }

    make_coordinator(
        factory,
        LateTerminalRunner([]),
        poll_interval=0,
        timeout_seconds=0.15,
    ).execute(run_id)

    state = snapshot(factory, run_id)
    assert state.run.status == "failed"
    finished = [
        event for event in state.events if event.event_type == "sandbox.finished"
    ]
    assert [event.payload for event in finished] == [
        {"status": "failed", "code": "sandbox_timeout"}
    ]


def test_team_watchdog_clamps_poll_sleep_to_remaining_deadline(run_factory):
    factory, run_id = run_factory
    sleep_calls = []

    def recording_sleep(seconds):
        sleep_calls.append(seconds)
        time.sleep(seconds)

    class RunningRunner(SequenceRunner):
        def status(self, current_run_id, *, monotonic_deadline=None):
            self.calls.append(("status", current_run_id))
            return {"run_id": current_run_id, "status": "running"}

    started_at = time.monotonic()
    make_coordinator(
        factory,
        RunningRunner([]),
        poll_interval=0.6,
        timeout_seconds=0.15,
        sleeper=recording_sleep,
    ).execute(run_id)

    assert sleep_calls
    assert max(sleep_calls) <= 0.15
    assert time.monotonic() - started_at < 0.45


def test_timeout_terminate_and_cleanup_share_one_control_allowance(run_factory):
    factory, run_id = run_factory

    class BlockingControlRunner(SequenceRunner):
        def status(self, current_run_id, *, monotonic_deadline=None):
            self.calls.append(("status", current_run_id))
            return {"run_id": current_run_id, "status": "running"}

        def terminate(self, current_run_id, *, monotonic_deadline=None):
            self.calls.append(("terminate", current_run_id))
            timeout = (
                0.9
                if monotonic_deadline is None
                else min(0.9, max(0, monotonic_deadline - time.monotonic()))
            )
            Event().wait(timeout=timeout)
            if (
                monotonic_deadline is not None
                and time.monotonic() >= monotonic_deadline
            ):
                raise RunnerUnavailableError("runner deadline expired")
            return {"run_id": current_run_id, "status": "terminated"}

        def cleanup(self, current_run_id, *, monotonic_deadline=None):
            self.calls.append(("cleanup", current_run_id))
            timeout = (
                0.9
                if monotonic_deadline is None
                else min(0.9, max(0, monotonic_deadline - time.monotonic()))
            )
            Event().wait(timeout=timeout)
            if (
                monotonic_deadline is not None
                and time.monotonic() >= monotonic_deadline
            ):
                raise RunnerUnavailableError("runner deadline expired")
            return {"run_id": current_run_id, "status": "cleaned"}

    runner = BlockingControlRunner([])
    started_at = time.monotonic()

    make_coordinator(
        factory,
        runner,
        poll_interval=0.01,
        timeout_seconds=0.05,
    ).execute(run_id)

    assert time.monotonic() - started_at < 1.4
    assert ("terminate", run_id) in runner.calls
    assert ("cleanup", run_id) in runner.calls


def test_timeout_worker_exit_remains_authoritative_when_completion_report_fails(
    run_factory,
):
    factory, run_id = run_factory
    runner = SequenceRunner(
        [{"status": "exited", "exit_code": 4, "oom_killed": False}]
    )

    make_coordinator(factory, runner, poll_interval=0).execute(run_id)

    state = snapshot(factory, run_id)
    assert state.run.status == "failed"
    assert next(
        event for event in state.events if event.event_type == "run.error"
    ).payload["code"] == "sandbox_timeout"


@pytest.mark.parametrize("failure_point", ["submit", "status"])
def test_coordinator_records_launcher_outage_without_leaking_exception(run_factory, failure_point):
    factory, run_id = run_factory
    error = RunnerUnavailableError("token=secret /var/run/docker.sock")
    runner = SequenceRunner(
        [{"status": "running"}],
        submit_error=error if failure_point == "submit" else None,
        status_error=error if failure_point == "status" else None,
    )

    make_coordinator(factory, runner, poll_interval=0).execute(run_id)

    state = snapshot(factory, run_id)
    event = next(event for event in state.events if event.event_type == "run.error")
    assert state.run.status == "failed"
    assert event.payload == {"code": "launcher_unavailable", "message": "沙箱运行服务暂不可用"}


def test_coordinator_keeps_terminal_state_when_cleanup_fails(run_factory):
    factory, run_id = run_factory
    runner = SequenceRunner(
        [{"status": "exited", "exit_code": 0, "oom_killed": False}],
        cleanup_error=RunnerUnavailableError("secret"),
    )

    make_coordinator(factory, runner, poll_interval=0).execute(run_id)

    state = snapshot(factory, run_id)
    assert state.run.status == "completed"
    assert state.events[-1].event_type == "sandbox.cleanup"
    assert state.events[-1].payload == {"status": "failed", "code": "launcher_unavailable"}


def test_coordinator_is_idempotent_after_run_reaches_terminal_state(run_factory):
    factory, run_id = run_factory
    runner = SequenceRunner([{"status": "exited", "exit_code": 0, "oom_killed": False}])
    coordinator = make_coordinator(factory, runner, poll_interval=0)

    coordinator.execute(run_id)
    coordinator.execute(run_id)

    assert [call for call in runner.calls if call[0] == "submit"] == [
        ("submit", run_id, "agent-v1", "runtime", {
            "snapshot_id": "snapshot-1",
            "snapshot_digest": "a" * 64,
            "gateway_url": "http://api:8000/internal/runner",
            "run_token": "run-secret-token",
            "deadline_at": ANY,
            "execution_deadline_at": ANY,
        }),
    ]


def test_coordinator_cancels_running_run_and_cleans_up(run_factory):
    factory, run_id = run_factory
    with factory.begin() as session:
        session.get(AgentRun, run_id).status = "running"
    runner = SequenceRunner([])

    make_coordinator(factory, runner, poll_interval=0).cancel(run_id)

    state = snapshot(factory, run_id)
    assert state.run.status == "cancelled"
    assert ("terminate", run_id) in runner.calls
    assert runner.calls[-1] == ("cleanup", run_id)
    assert next(event for event in state.events if event.event_type == "run.error").payload["code"] == "sandbox_cancelled"


def test_cancellation_wins_when_container_exit_is_observed_during_terminate(
    run_factory,
):
    factory, run_id = run_factory
    with factory.begin() as session:
        session.get(AgentRun, run_id).status = "running"

    class RacingRunner(SequenceRunner):
        coordinator = None

        def terminate(self, current_run_id):
            self.calls.append(("terminate", current_run_id))
            self.coordinator._apply_container_status(
                current_run_id,
                {"status": "exited", "exit_code": 1, "oom_killed": False},
            )
            return {"run_id": current_run_id, "status": "terminated"}

    runner = RacingRunner([])
    coordinator = make_coordinator(factory, runner, poll_interval=0)
    runner.coordinator = coordinator

    coordinator.cancel(run_id)

    state = snapshot(factory, run_id)
    assert state.run.status == "cancelled"
    assert [
        event.payload.get("code")
        for event in state.events
        if event.event_type == "run.error"
    ] == ["sandbox_cancelled"]


def test_coordinator_recovers_running_run_without_resubmitting(run_factory):
    factory, run_id = run_factory
    with factory.begin() as session:
        session.get(AgentRun, run_id).status = "running"
    runner = SequenceRunner([{"status": "exited", "exit_code": 137, "oom_killed": True}])

    make_coordinator(factory, runner, poll_interval=0).recover(run_id)

    state = snapshot(factory, run_id)
    assert state.run.status == "failed"
    assert next(event for event in state.events if event.event_type == "run.error").payload["code"] == "sandbox_oom"
    assert not [call for call in runner.calls if call[0] == "submit"]
    assert runner.calls[-1] == ("cleanup", run_id)


def test_coordinator_waits_for_recovered_running_container_to_exit(run_factory):
    factory, run_id = run_factory
    with factory.begin() as session:
        session.get(AgentRun, run_id).status = "running"
    runner = SequenceRunner(
        [
            {"status": "running"},
            {"status": "exited", "exit_code": 0, "oom_killed": False},
        ]
    )

    make_coordinator(factory, runner, poll_interval=0).recover(run_id)

    state = snapshot(factory, run_id)
    assert state.run.status == "completed"
    assert not [call for call in runner.calls if call[0] == "submit"]
    assert [call for call in runner.calls if call[0] == "status"] == [
        ("status", run_id),
        ("status", run_id),
    ]
    assert runner.calls[-1] == ("cleanup", run_id)


def test_recovered_team_run_keeps_original_snapshot_deadline(run_factory):
    factory, run_id = run_factory
    base = datetime(2026, 9, 4, 8, 0, tzinfo=UTC)
    snapshots = FakeSnapshots(
        actor=SimpleNamespace(kind="team", timeout_seconds=1),
        created_at=base,
    )
    with factory.begin() as session:
        session.get(AgentRun, run_id).status = "running"
    runner = SequenceRunner([{"status": "running"}])

    make_coordinator(
        factory,
        runner,
        snapshots=snapshots,
        poll_interval=0,
        timeout_seconds=10,
        monotonic=iter([0.0, 0.0]).__next__,
        clock=lambda: base + timedelta(seconds=2),
    ).recover(run_id)

    state = snapshot(factory, run_id)
    assert state.run.status == "failed"
    assert not [call for call in runner.calls if call[0] == "status"]
    assert ("terminate", run_id) in runner.calls


def test_coordinator_lists_only_running_runs_for_startup_recovery(run_factory):
    factory, running_id = run_factory
    with factory.begin() as session:
        session.get(AgentRun, running_id).status = "running"
        original = session.get(AgentRun, running_id)
        queued = AgentRun(
            conversation_id=original.conversation_id,
            trigger_message_id=original.trigger_message_id,
            actor_type="agent",
            actor_id="agent-v2",
            actor_roles_json=["user"],
            status="queued",
        )
        session.add(queued)
        session.flush()
        queued_id = queued.id

    assert make_coordinator(factory, SequenceRunner([])).list_recoverable_run_ids() == [running_id]
    assert queued_id not in make_coordinator(factory, SequenceRunner([])).list_recoverable_run_ids()


def test_coordinator_retries_failed_cleanup_for_terminal_run(run_factory):
    factory, run_id = run_factory
    with factory.begin() as session:
        session.get(AgentRun, run_id).status = "failed"
        session.add(RunEvent(
            run_id=run_id,
            sequence=2,
            event_type="sandbox.cleanup",
            payload={"status": "failed", "code": "launcher_unavailable"},
        ))
    runner = SequenceRunner([])
    coordinator = make_coordinator(factory, runner)

    assert coordinator.list_cleanup_retry_run_ids() == [run_id]
    coordinator.retry_cleanup(run_id)

    state = snapshot(factory, run_id)
    assert state.run.status == "failed"
    assert state.events[-1].event_type == "sandbox.cleanup"
    assert state.events[-1].payload == {"status": "cleaned"}


def test_cleanup_runs_again_after_a_new_sandbox_execution_started(run_factory):
    factory, run_id = run_factory
    with factory.begin() as session:
        session.get(AgentRun, run_id).status = "failed"
        session.add_all(
            [
                RunEvent(
                    run_id=run_id,
                    sequence=2,
                    event_type="sandbox.cleanup",
                    payload={"status": "cleaned"},
                ),
                RunEvent(
                    run_id=run_id,
                    sequence=3,
                    event_type="sandbox.started",
                    payload={"status": "started"},
                ),
            ]
        )
    runner = SequenceRunner([])
    coordinator = make_coordinator(factory, runner)

    assert coordinator.list_cleanup_retry_run_ids() == [run_id]
    coordinator.retry_cleanup(run_id)

    state = snapshot(factory, run_id)
    cleanup_events = [
        event for event in state.events if event.event_type == "sandbox.cleanup"
    ]
    assert len(cleanup_events) == 2
    assert cleanup_events[-1].payload == {"status": "cleaned"}
    assert runner.calls == [("cleanup", run_id)]


def test_coordinator_deduplicates_concurrent_cleanup_retries(run_factory):
    factory, run_id = run_factory
    with factory.begin() as session:
        session.get(AgentRun, run_id).status = "failed"
        session.add(RunEvent(
            run_id=run_id,
            sequence=2,
            event_type="sandbox.cleanup",
            payload={"status": "failed", "code": "launcher_unavailable"},
        ))

    class CleanupRaceRunner(SequenceRunner):
        def cleanup(self, current_run_id):
            self.calls.append(("cleanup", current_run_id))
            if len([call for call in self.calls if call[0] == "cleanup"]) > 1:
                raise RunnerUnavailableError("container is not registered for run")
            return {"run_id": current_run_id, "status": "cleaned"}

    runner = CleanupRaceRunner([])
    coordinator = make_coordinator(factory, runner)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(coordinator.retry_cleanup, run_id) for _ in range(2)]
        for future in futures:
            future.result()

    state = snapshot(factory, run_id)
    cleanup_events = [event for event in state.events if event.event_type == "sandbox.cleanup"]
    assert cleanup_events[-1].payload == {"status": "cleaned"}
    assert len([call for call in runner.calls if call[0] == "cleanup"]) == 1


def test_coordinator_issues_snapshot_and_token_before_submit(run_factory):
    factory, run_id = run_factory
    runner = SequenceRunner([{"status": "exited", "exit_code": 0, "oom_killed": False}])
    snapshots = FakeSnapshots()
    tokens = FakeTokens()

    make_coordinator(
        factory,
        runner,
        snapshots=snapshots,
        tokens=tokens,
        poll_interval=0,
    ).execute(run_id)

    submission = next(call for call in runner.calls if call[0] == "submit")[4]
    assert submission["snapshot_id"] == snapshots.created[0].snapshot_id
    assert submission["snapshot_digest"] == snapshots.created[0].digest
    assert submission["run_token"] == tokens.issued[0].value
    assert "artifact.create" in tokens.issued[0].actions


@pytest.mark.parametrize(
    ("status_payload", "reason"),
    [
        ({"status": "exited", "exit_code": 0, "oom_killed": False}, "completed"),
        ({"status": "exited", "exit_code": 2, "oom_killed": False}, "sandbox_failed"),
        ({"status": "exited", "exit_code": 137, "oom_killed": True}, "sandbox_oom"),
        ({"status": "terminated"}, "sandbox_cancelled"),
    ],
)
def test_every_container_terminal_path_revokes_token(run_factory, status_payload, reason):
    factory, run_id = run_factory
    tokens = FakeTokens()

    make_coordinator(
        factory,
        SequenceRunner([status_payload]),
        tokens=tokens,
        poll_interval=0,
    ).execute(run_id)

    assert tokens.revoked == [(run_id, reason)]


def test_container_exit_does_not_duplicate_gateway_completion(run_factory):
    factory, run_id = run_factory
    tokens = FakeTokens()

    def complete_from_gateway(current_run_id):
        with factory.begin() as session:
            repository = ConversationRepository(session)
            run = repository.get_run_by_id(current_run_id)
            repository.add_assistant_message(current_run_id, "gateway result")
            repository.append_event(
                current_run_id,
                "runner.completion",
                {"status": "completed", "artifact_refs": []},
            )
            run.status = "completed"
            repository.append_event(
                current_run_id,
                "run.status",
                {"status": "completed"},
            )

    runner = SequenceRunner(
        [{"status": "exited", "exit_code": 0, "oom_killed": False}],
        status_callback=complete_from_gateway,
    )

    make_coordinator(
        factory,
        runner,
        tokens=tokens,
        poll_interval=0,
    ).execute(run_id)

    state = snapshot(factory, run_id)
    assert state.run.status == "completed"
    assert [event.event_type for event in state.events].count("runner.completion") == 1
    assert "sandbox.finished" not in [event.event_type for event in state.events]
    assert tokens.revoked == [(run_id, "completed")]


def test_interrupted_gateway_completion_survives_container_exit_and_revokes_token(run_factory):
    factory, run_id = run_factory
    tokens = FakeTokens()

    def interrupt_from_gateway(current_run_id):
        with factory.begin() as session:
            repository = ConversationRepository(session)
            run = repository.get_run_by_id(current_run_id)
            repository.append_event(
                current_run_id,
                "runner.completion",
                {
                    "status": "interrupted",
                    "error_code": "approval_required",
                    "artifact_refs": [],
                },
            )
            run.status = "waiting_approval"
            repository.append_event(
                current_run_id,
                "run.status",
                {"status": "waiting_approval"},
            )

    runner = SequenceRunner(
        [{"status": "exited", "exit_code": 0, "oom_killed": False}],
        status_callback=interrupt_from_gateway,
    )

    make_coordinator(
        factory,
        runner,
        tokens=tokens,
        poll_interval=0,
    ).execute(run_id)

    state = snapshot(factory, run_id)
    assert state.run.status == "waiting_approval"
    assert "sandbox.finished" not in [event.event_type for event in state.events]
    assert tokens.revoked == [(run_id, "approval_required")]


def test_failed_container_exit_supersedes_waiting_approval(run_factory):
    factory, run_id = run_factory

    def wait_for_approval(current_run_id):
        with factory.begin() as session:
            repository = ConversationRepository(session)
            run = repository.get_run_by_id(current_run_id)
            run.status = "waiting_approval"
            repository.append_event(
                current_run_id,
                "run.status",
                {"status": "waiting_approval"},
            )

    runner = SequenceRunner(
        [{"status": "exited", "exit_code": 1, "oom_killed": False}],
        status_callback=wait_for_approval,
    )

    make_coordinator(factory, runner, poll_interval=0).execute(run_id)

    state = snapshot(factory, run_id)
    assert state.run.status == "failed"
    assert next(
        event for event in state.events if event.event_type == "run.error"
    ).payload["code"] == "sandbox_failed"


def test_waiting_approval_run_can_be_cancelled(run_factory):
    factory, run_id = run_factory
    with factory.begin() as session:
        session.get(AgentRun, run_id).status = "waiting_approval"
    runner = SequenceRunner([])

    make_coordinator(factory, runner, poll_interval=0).cancel(run_id)

    state = snapshot(factory, run_id)
    assert state.run.status == "cancelled"
    assert next(
        event for event in state.events if event.event_type == "run.error"
    ).payload["code"] == "sandbox_cancelled"


def test_concurrent_terminal_transitions_persist_only_one_result(
    run_factory, monkeypatch
):
    factory, run_id = run_factory
    with factory.begin() as session:
        session.get(AgentRun, run_id).status = "running"
    coordinator = make_coordinator(factory, SequenceRunner([]), poll_interval=0)
    barrier = Barrier(2)
    thread_state = local()
    original_get = ConversationRepository.get_run_by_id

    def synchronized_get(repository, current_run_id):
        run = original_get(repository, current_run_id)
        if (
            run is not None
            and run.status == "running"
            and not getattr(thread_state, "synchronized", False)
        ):
            thread_state.synchronized = True
            barrier.wait(timeout=5)
        return run

    monkeypatch.setattr(ConversationRepository, "get_run_by_id", synchronized_get)

    with ThreadPoolExecutor(max_workers=2) as executor:
        completed = executor.submit(coordinator._finish, run_id, "completed")
        cancelled = executor.submit(
            coordinator._finish, run_id, "cancelled", "sandbox_cancelled"
        )
        completed.result()
        cancelled.result()

    state = snapshot(factory, run_id)
    finished = [
        event for event in state.events if event.event_type == "sandbox.finished"
    ]
    assert len(finished) == 1
    assert finished[0].payload["status"] == state.run.status
