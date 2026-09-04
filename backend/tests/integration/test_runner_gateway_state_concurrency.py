import hashlib
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete, event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.approvals.models import Approval
from app.approvals.service import arguments_digest
from app.audit.models import AuditEvent
from app.conversations.models import (
    AgentRun,
    Conversation,
    Message,
    RunEvent,
    ToolInvocation,
)
from app.conversations.repository import ConversationRepository
from app.runtime.checkpoint_store import (
    CheckpointStore,
    RuntimeCheckpoint,
    RuntimeRunnerRequest,
)
from app.runtime.execution_snapshot import (
    ExecutionSnapshotPayload,
    PublishedAgentSnapshot,
    PublishedTeamSnapshot,
    RuntimeExecutionSnapshot,
    SnapshotModelSelection,
    SnapshotRuntimeLimits,
    SnapshotTeamMember,
    StoredExecutionSnapshot,
    canonical_snapshot_bytes,
)
from app.runtime.run_tokens import RunTokenClaims
from app.runtime.runner_gateway_auth import RunnerGatewayError
from app.runtime.runner_gateway_schemas import (
    ArtifactCapabilityRegistrationRequest,
    CheckpointWriteRequest,
    CompletionRequest,
    EventAppendRequest,
)
from app.runtime.runner_gateway_service import RunnerGatewayService
from app.tools.builtins import BUILTIN_EXECUTORS, BUILTIN_TOOL_DEFINITIONS
from app.tools.gateway import ToolGateway
from app.tools.schemas import ToolExecutionContext, ToolRuntimeError
from app.tools.store import ToolStore


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="requires PostgreSQL",
)


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if database_url is None:
        yield
        return
    subprocess.run(
        (
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "backend/alembic.ini",
            "upgrade",
            "head",
        ),
        check=True,
        env=os.environ | {"DATABASE_URL": database_url},
        timeout=60,
    )
    yield


class StaticSnapshotService:
    def __init__(self, snapshot: StoredExecutionSnapshot) -> None:
        self.snapshot = snapshot

    def get(self, snapshot_id: str) -> StoredExecutionSnapshot | None:
        if snapshot_id == self.snapshot.snapshot_id:
            return self.snapshot
        return None


class EmptyArtifactService:
    def get_for_run(self, _run_id: str, _artifact_id: str):
        raise AssertionError("completion fixture has no artifact references")


class CallbackLockBarrierService(RunnerGatewayService):
    def __init__(
        self,
        *args,
        lock_acquired: threading.Event,
        release_lock: threading.Event,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.lock_acquired = lock_acquired
        self.release_lock = release_lock

    def _require_active_run(self, run: AgentRun) -> None:
        super()._require_active_run(run)
        self.lock_acquired.set()
        if not self.release_lock.wait(10):
            raise TimeoutError("callback Run lock was not released")


class CompletionLockBarrierService(RunnerGatewayService):
    def __init__(
        self,
        *args,
        lock_acquired: threading.Event,
        release_lock: threading.Event,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.lock_acquired = lock_acquired
        self.release_lock = release_lock

    def _lock_run(
        self, repository: ConversationRepository, run_id: str
    ) -> AgentRun:
        run = super()._lock_run(repository, run_id)
        self.lock_acquired.set()
        if not self.release_lock.wait(10):
            raise TimeoutError("completion Run lock was not released")
        return run


def _snapshot(run_id: str) -> StoredExecutionSnapshot:
    created_at = datetime(2026, 9, 4, 10, 0, tzinfo=UTC)
    payload = ExecutionSnapshotPayload(
        snapshot_id=str(uuid.uuid4()),
        run_id=run_id,
        unit_id="integration-unit",
        project_id="integration-project",
        user_id="integration-user",
        actor=PublishedAgentSnapshot(
            id="integration-agent",
            name="Integration Agent",
            description="",
            runtime_form="common",
            language="zh-CN",
            system_prompt="",
            context_prompt="",
            approval_policy="never",
        ),
        model=SnapshotModelSelection(
            provider_id="integration-provider",
            model="integration-model",
        ),
        messages=(),
        limits=SnapshotRuntimeLimits(snapshot_max_bytes=1_048_576),
        created_at=created_at,
    )
    digest = hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest()
    return StoredExecutionSnapshot(
        snapshot_id=payload.snapshot_id,
        run_id=run_id,
        digest=digest,
        payload=payload,
        created_at=created_at,
        expires_at=None,
    )


def _claims(snapshot: StoredExecutionSnapshot) -> RunTokenClaims:
    return RunTokenClaims(
        iss="iap-api",
        aud="iap-runner-gateway",
        jti=str(uuid.uuid4()),
        run_id=snapshot.run_id,
        unit_id=snapshot.payload.unit_id,
        project_id=snapshot.payload.project_id,
        snapshot_id=snapshot.snapshot_id,
        snapshot_digest=snapshot.digest,
        actions=("checkpoint.write", "event.append", "result.complete"),
        iat=1,
        nbf=1,
        exp=9_999_999_999,
    )


def _team_snapshot(
    run_id: str,
    *,
    created_at: datetime | None = None,
    timeout_seconds: int = 60,
) -> StoredExecutionSnapshot:
    created_at = created_at or datetime(2026, 9, 4, 10, 0, tzinfo=UTC)
    model = SnapshotModelSelection(
        provider_id="integration-provider",
        model="integration-model",
    )

    def member(agent_id: str, role: str) -> SnapshotTeamMember:
        agent = PublishedAgentSnapshot(
            id=agent_id,
            name=agent_id,
            description="",
            runtime_form="common",
            language="zh-CN",
            system_prompt="",
            context_prompt="",
            approval_policy="never",
        )
        return SnapshotTeamMember(
            agent_id=agent_id,
            role=role,
            responsibility=role,
            agent_definition_digest=hashlib.sha256(
                agent_id.encode("utf-8")
            ).hexdigest(),
            agent=agent,
            model=model,
        )

    actor = PublishedTeamSnapshot(
        id="integration-team",
        version_id="integration-team-version",
        version=1,
        definition_digest="d" * 64,
        supervisor=member("supervisor", "supervisor"),
        members=(member("forecast", "member"),),
        max_steps=2,
        max_parallel_members=1,
        timeout_seconds=timeout_seconds,
        failure_strategy="fail_fast",
        name="Integration Team",
        description="",
        runtime_form="common",
        language="zh-CN",
        system_prompt="",
        context_prompt="",
        approval_policy="never",
    )
    payload = ExecutionSnapshotPayload(
        schema_version="5",
        snapshot_id=str(uuid.uuid4()),
        run_id=run_id,
        unit_id="integration-unit",
        project_id="integration-project",
        user_id="integration-user",
        actor=actor,
        model=model,
        messages=(),
        limits=SnapshotRuntimeLimits(snapshot_max_bytes=1_048_576),
        created_at=created_at,
    )
    digest = hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest()
    return StoredExecutionSnapshot(
        snapshot_id=payload.snapshot_id,
        run_id=run_id,
        digest=digest,
        payload=payload,
        created_at=created_at,
        expires_at=None,
    )


def _team_scheduler_state(snapshot: StoredExecutionSnapshot) -> dict[str, object]:
    return {
        "kind": "team_scheduler",
        "stage": "executing",
        "snapshot_digest": snapshot.digest,
        "team_version_id": "integration-team-version",
        "plan": {
            "tasks": [
                {
                    "id": "forecast-task",
                    "member_id": "forecast",
                    "objective": "forecast",
                    "depends_on": [],
                    "position": 0,
                }
            ]
        },
        "pending_task_ids": ["forecast-task"],
        "started_task_ids": ["forecast-task"],
        "active_task_id": "forecast-task",
        "active_member_agent_id": "forecast",
        "active_invocation_id": "integration:forecast-task",
        "completed_results": [],
        "failed_results": [],
        "event_sequence": 0,
        "checkpoint_revision": 1,
        "budget": {
            "next_invocation_sequence": 0,
            "tool_call_count": 0,
            "subagent_call_count": 0,
        },
    }


def _service(
    session: Session,
    snapshot: StoredExecutionSnapshot,
    service_type=RunnerGatewayService,
    **kwargs,
) -> RunnerGatewayService:
    return service_type(
        StaticSnapshotService(snapshot),
        checkpoint_store=CheckpointStore(session),
        conversation_repository=ConversationRepository(session),
        artifact_service=EmptyArtifactService(),
        **kwargs,
    )


def _invoke_callback(
    service: RunnerGatewayService,
    callback_type: str,
    run_id: str,
    claims: RunTokenClaims,
    idempotency_key: str,
):
    if callback_type == "checkpoint":
        return service.save_checkpoint(
            run_id,
            "race-checkpoint",
            CheckpointWriteRequest(state={"status": "callback-committed"}),
            claims,
            idempotency_key,
        )
    return service.append_event(
        run_id,
        EventAppendRequest(
            sequence=1,
            event_type="race.callback",
            payload={"status": "callback-committed"},
        ),
        claims,
        idempotency_key,
    )


def _complete(
    service: RunnerGatewayService,
    run_id: str,
    claims: RunTokenClaims,
):
    return service.complete(
        run_id,
        CompletionRequest(status="failed", error_code="sandbox_timeout"),
        claims,
        f"completion:{run_id}",
    )


def _register_artifact_capability(
    service: RunnerGatewayService,
    run_id: str,
    claims: RunTokenClaims,
):
    return service.register_artifact_capability(
        run_id,
        ArtifactCapabilityRegistrationRequest(
            team_version_id="integration-team-version",
            member_agent_id="forecast",
            task_id="forecast-task",
            invocation_id="integration:forecast-task",
        ),
        claims,
    )


def _wait_until_row_lock_blocks(factory, blocked_pid: int) -> None:
    deadline = time.monotonic() + 5
    with factory() as observer:
        while time.monotonic() < deadline:
            blocker_count = observer.scalar(
                text("SELECT cardinality(pg_blocking_pids(:pid))"),
                {"pid": blocked_pid},
            )
            if blocker_count:
                return
            time.sleep(0.01)
    raise AssertionError("request did not block on the Run row lock")


@pytest.fixture
def state_race_environment():
    database_url = os.environ["TEST_DATABASE_URL"]
    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": 5,
            "options": "-c lock_timeout=7000 -c statement_timeout=10000",
        },
    )
    factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
        class_=Session,
    )
    run_id = str(uuid.uuid4())
    conversation_id = str(uuid.uuid4())
    message_id = str(uuid.uuid4())
    snapshot = _snapshot(run_id)
    claims = _claims(snapshot)

    with factory.begin() as seed:
        seed.add(
            Conversation(
                id=conversation_id,
                unit_id=snapshot.payload.unit_id,
                project_id=snapshot.payload.project_id,
                owner_id=snapshot.payload.user_id,
                title="Runner state race",
            )
        )
        seed.flush()
        seed.add(
            Message(
                id=message_id,
                conversation_id=conversation_id,
                sequence=1,
                role="user",
                content="race",
            )
        )
        seed.flush()
        seed.add(
            AgentRun(
                id=run_id,
                conversation_id=conversation_id,
                trigger_message_id=message_id,
                actor_type="agent",
                actor_id="integration-agent",
                actor_roles_json=[],
                status="running",
            )
        )

    yield factory, run_id, snapshot, claims

    with factory.begin() as cleanup:
        cleanup.execute(
            delete(AuditEvent).where(AuditEvent.run_id == run_id)
        )
        cleanup.execute(
            delete(RuntimeRunnerRequest).where(
                RuntimeRunnerRequest.run_id == run_id
            )
        )
        cleanup.execute(
            delete(RuntimeCheckpoint).where(RuntimeCheckpoint.run_id == run_id)
        )
        cleanup.execute(
            delete(RuntimeExecutionSnapshot).where(
                RuntimeExecutionSnapshot.run_id == run_id
            )
        )
        cleanup.execute(delete(RunEvent).where(RunEvent.run_id == run_id))
        cleanup.execute(delete(AgentRun).where(AgentRun.id == run_id))
        cleanup.execute(delete(Message).where(Message.id == message_id))
        cleanup.execute(
            delete(Conversation).where(Conversation.id == conversation_id)
        )
    engine.dispose()


@pytest.mark.parametrize("callback_type", ["checkpoint", "event"])
def test_callback_commit_serializes_before_terminal_completion_and_remains_replayable(
    state_race_environment,
    callback_type,
):
    factory, run_id, snapshot, claims = state_race_environment
    callback_has_lock = threading.Event()
    release_callback = threading.Event()
    completion_pid_ready = threading.Event()
    outcomes: dict[str, object] = {}

    def run_callback() -> None:
        try:
            with factory() as session:
                service = _service(
                    session,
                    snapshot,
                    CallbackLockBarrierService,
                    lock_acquired=callback_has_lock,
                    release_lock=release_callback,
                )
                outcomes["callback"] = _invoke_callback(
                    service,
                    callback_type,
                    run_id,
                    claims,
                    f"{callback_type}:before-timeout",
                )
        except BaseException as error:
            outcomes["callback_error"] = error

    def run_completion() -> None:
        try:
            with factory() as session:
                outcomes["completion_pid"] = session.scalar(
                    text("SELECT pg_backend_pid()")
                )
                completion_pid_ready.set()
                outcomes["completion"] = _complete(
                    _service(session, snapshot), run_id, claims
                )
        except BaseException as error:
            outcomes["completion_error"] = error

    callback_thread = threading.Thread(target=run_callback, daemon=True)
    completion_thread = threading.Thread(target=run_completion, daemon=True)
    callback_thread.start()
    try:
        assert callback_has_lock.wait(5), "callback did not acquire the Run lock"
        completion_thread.start()
        assert completion_pid_ready.wait(5), "completion did not start"
        _wait_until_row_lock_blocks(factory, int(outcomes["completion_pid"]))
    finally:
        release_callback.set()
        callback_thread.join(10)
        completion_thread.join(10)

    assert not callback_thread.is_alive()
    assert not completion_thread.is_alive()
    assert "callback_error" not in outcomes
    assert "completion_error" not in outcomes
    assert outcomes["completion"].status == "failed"

    with factory() as replay_session:
        replay = _invoke_callback(
            _service(replay_session, snapshot),
            callback_type,
            run_id,
            claims,
            f"{callback_type}:before-timeout",
        )
    assert replay == outcomes["callback"]

    with factory() as verification:
        assert verification.get(AgentRun, run_id).status == "failed"
        assert verification.scalar(
            select(func.count())
            .select_from(RuntimeRunnerRequest)
            .where(
                RuntimeRunnerRequest.run_id == run_id,
                RuntimeRunnerRequest.action
                == f"{callback_type}.{'write' if callback_type == 'checkpoint' else 'append'}",
            )
        ) == 1
        if callback_type == "checkpoint":
            assert verification.scalar(
                select(func.count())
                .select_from(RuntimeCheckpoint)
                .where(RuntimeCheckpoint.run_id == run_id)
            ) == 1
        else:
            assert verification.scalar(
                select(func.count())
                .select_from(RunEvent)
                .where(
                    RunEvent.run_id == run_id,
                    RunEvent.event_type == "race.callback",
                )
            ) == 1


@pytest.mark.parametrize("callback_type", ["checkpoint", "event"])
def test_terminal_completion_serializes_before_callback_and_rejects_late_state(
    state_race_environment,
    callback_type,
):
    factory, run_id, snapshot, claims = state_race_environment
    completion_has_lock = threading.Event()
    release_completion = threading.Event()
    callback_pid_ready = threading.Event()
    outcomes: dict[str, object] = {}

    def run_completion() -> None:
        try:
            with factory() as session:
                service = _service(
                    session,
                    snapshot,
                    CompletionLockBarrierService,
                    lock_acquired=completion_has_lock,
                    release_lock=release_completion,
                )
                outcomes["completion"] = _complete(service, run_id, claims)
        except BaseException as error:
            outcomes["completion_error"] = error

    def run_callback() -> None:
        try:
            with factory() as session:
                outcomes["callback_pid"] = session.scalar(
                    text("SELECT pg_backend_pid()")
                )
                callback_pid_ready.set()
                outcomes["callback"] = _invoke_callback(
                    _service(session, snapshot),
                    callback_type,
                    run_id,
                    claims,
                    f"{callback_type}:after-timeout",
                )
        except BaseException as error:
            outcomes["callback_error"] = error

    completion_thread = threading.Thread(target=run_completion, daemon=True)
    callback_thread = threading.Thread(target=run_callback, daemon=True)
    completion_thread.start()
    try:
        assert completion_has_lock.wait(5), "completion did not acquire the Run lock"
        callback_thread.start()
        assert callback_pid_ready.wait(5), "callback did not start"
        _wait_until_row_lock_blocks(factory, int(outcomes["callback_pid"]))
    finally:
        release_completion.set()
        completion_thread.join(10)
        callback_thread.join(10)

    assert not completion_thread.is_alive()
    assert not callback_thread.is_alive()
    assert "completion_error" not in outcomes
    assert outcomes["completion"].status == "failed"
    assert isinstance(outcomes.get("callback_error"), RunnerGatewayError)
    callback_error = outcomes["callback_error"]
    assert callback_error.status_code == 409
    assert callback_error.code == "run_not_active"

    with factory() as verification:
        assert verification.get(AgentRun, run_id).status == "failed"
        assert verification.scalar(
            select(func.count())
            .select_from(RuntimeRunnerRequest)
            .where(
                RuntimeRunnerRequest.run_id == run_id,
                RuntimeRunnerRequest.action
                == f"{callback_type}.{'write' if callback_type == 'checkpoint' else 'append'}",
            )
        ) == 0
        if callback_type == "checkpoint":
            assert verification.scalar(
                select(func.count())
                .select_from(RuntimeCheckpoint)
                .where(RuntimeCheckpoint.run_id == run_id)
            ) == 0
        else:
            assert verification.scalar(
                select(func.count())
                .select_from(RunEvent)
                .where(
                    RunEvent.run_id == run_id,
                    RunEvent.event_type == "race.callback",
                )
            ) == 0


def test_artifact_capability_commit_serializes_before_terminal_completion(
    state_race_environment,
):
    factory, run_id, _agent_snapshot, _agent_claims = state_race_environment
    snapshot = _team_snapshot(run_id)
    claims = _claims(snapshot)
    with factory() as setup:
        CheckpointStore(setup).save(
            run_id,
            "team-scheduler",
            _team_scheduler_state(snapshot),
            snapshot.digest,
            "checkpoint:team-scheduler",
        )

    capability_has_lock = threading.Event()
    release_capability = threading.Event()
    completion_pid_ready = threading.Event()
    outcomes: dict[str, object] = {}

    def run_capability() -> None:
        try:
            with factory() as session:
                service = _service(
                    session,
                    snapshot,
                    CallbackLockBarrierService,
                    lock_acquired=capability_has_lock,
                    release_lock=release_capability,
                )
                outcomes["capability"] = _register_artifact_capability(
                    service, run_id, claims
                )
        except BaseException as error:
            outcomes["capability_error"] = error

    def run_completion() -> None:
        try:
            with factory() as session:
                outcomes["completion_pid"] = session.scalar(
                    text("SELECT pg_backend_pid()")
                )
                completion_pid_ready.set()
                outcomes["completion"] = _complete(
                    _service(session, snapshot), run_id, claims
                )
        except BaseException as error:
            outcomes["completion_error"] = error

    capability_thread = threading.Thread(target=run_capability, daemon=True)
    completion_thread = threading.Thread(target=run_completion, daemon=True)
    capability_thread.start()
    try:
        assert capability_has_lock.wait(5), "capability did not acquire Run lock"
        completion_thread.start()
        assert completion_pid_ready.wait(5), "completion did not start"
        _wait_until_row_lock_blocks(factory, int(outcomes["completion_pid"]))
    finally:
        release_capability.set()
        capability_thread.join(10)
        completion_thread.join(10)

    assert not capability_thread.is_alive()
    assert not completion_thread.is_alive()
    assert "capability_error" not in outcomes
    assert "completion_error" not in outcomes
    assert len(outcomes["capability"].capability) >= 32
    assert outcomes["completion"].status == "failed"
    with factory() as verification:
        assert verification.scalar(
            select(func.count())
            .select_from(RuntimeCheckpoint)
            .where(RuntimeCheckpoint.run_id == run_id)
        ) == 2


def test_terminal_completion_serializes_before_artifact_capability_and_rejects_it(
    state_race_environment,
):
    factory, run_id, _agent_snapshot, _agent_claims = state_race_environment
    snapshot = _team_snapshot(run_id)
    claims = _claims(snapshot)
    with factory() as setup:
        CheckpointStore(setup).save(
            run_id,
            "team-scheduler",
            _team_scheduler_state(snapshot),
            snapshot.digest,
            "checkpoint:team-scheduler",
        )

    completion_has_lock = threading.Event()
    release_completion = threading.Event()
    capability_pid_ready = threading.Event()
    outcomes: dict[str, object] = {}

    def run_completion() -> None:
        try:
            with factory() as session:
                service = _service(
                    session,
                    snapshot,
                    CompletionLockBarrierService,
                    lock_acquired=completion_has_lock,
                    release_lock=release_completion,
                )
                outcomes["completion"] = _complete(service, run_id, claims)
        except BaseException as error:
            outcomes["completion_error"] = error

    def run_capability() -> None:
        try:
            with factory() as session:
                outcomes["capability_pid"] = session.scalar(
                    text("SELECT pg_backend_pid()")
                )
                capability_pid_ready.set()
                outcomes["capability"] = _register_artifact_capability(
                    _service(session, snapshot), run_id, claims
                )
        except BaseException as error:
            outcomes["capability_error"] = error

    completion_thread = threading.Thread(target=run_completion, daemon=True)
    capability_thread = threading.Thread(target=run_capability, daemon=True)
    completion_thread.start()
    try:
        assert completion_has_lock.wait(5), "completion did not acquire Run lock"
        capability_thread.start()
        assert capability_pid_ready.wait(5), "capability did not start"
        _wait_until_row_lock_blocks(factory, int(outcomes["capability_pid"]))
    finally:
        release_completion.set()
        completion_thread.join(10)
        capability_thread.join(10)

    assert not completion_thread.is_alive()
    assert not capability_thread.is_alive()
    assert "completion_error" not in outcomes
    assert outcomes["completion"].status == "failed"
    assert isinstance(outcomes.get("capability_error"), RunnerGatewayError)
    capability_error = outcomes["capability_error"]
    assert capability_error.status_code == 409
    assert capability_error.code == "run_not_active"
    with factory() as verification:
        assert verification.scalar(
            select(func.count())
            .select_from(RuntimeCheckpoint)
            .where(RuntimeCheckpoint.run_id == run_id)
        ) == 1


def test_team_completion_rechecks_deadline_after_blocked_post_write_flush(
    state_race_environment,
    monkeypatch,
):
    from app.runtime import runner_gateway_service as service_module

    factory, run_id, _agent_snapshot, _agent_claims = state_race_environment
    created_at = datetime(2026, 9, 4, 10, 0, tzinfo=UTC)
    snapshot = _team_snapshot(run_id, created_at=created_at)
    claims = _claims(snapshot)
    current_time = [created_at.replace(second=59)]
    flush_started = threading.Event()
    release_flush = threading.Event()
    outcomes: dict[str, object] = {}

    class MutableDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return current_time[0]

    monkeypatch.setattr(service_module, "datetime", MutableDatetime)

    def block_completion_flush(session, _flush_context, _instances):
        if any(
            isinstance(item, RunEvent) and item.event_type == "runner.completion"
            for item in session.new
        ):
            flush_started.set()
            if not release_flush.wait(10):
                raise TimeoutError("completion flush was not released")

    def run_completion() -> None:
        try:
            with factory() as session:
                event.listen(session, "before_flush", block_completion_flush)
                outcomes["completion"] = _service(session, snapshot).complete(
                    run_id,
                    CompletionRequest(
                        status="completed",
                        final_assistant_content="late Team result",
                    ),
                    claims,
                    f"completion:{run_id}:late-success",
                )
        except BaseException as error:
            outcomes["completion_error"] = error

    completion_thread = threading.Thread(target=run_completion, daemon=True)
    completion_thread.start()
    try:
        assert flush_started.wait(5), "completion did not reach its final flush"
        current_time[0] = created_at.replace(minute=1, second=1)
    finally:
        release_flush.set()
        completion_thread.join(10)

    assert not completion_thread.is_alive()
    assert "completion" not in outcomes
    assert isinstance(outcomes.get("completion_error"), RunnerGatewayError)
    completion_error = outcomes["completion_error"]
    assert completion_error.status_code == 409
    assert completion_error.code == "sandbox_timeout"

    with factory() as verification:
        assert verification.get(AgentRun, run_id).status == "running"
        assert verification.scalar(
            select(func.count())
            .select_from(RuntimeRunnerRequest)
            .where(RuntimeRunnerRequest.run_id == run_id)
        ) == 0
        assert verification.scalar(
            select(func.count())
            .select_from(RunEvent)
            .where(RunEvent.run_id == run_id)
        ) == 0
        assert verification.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.run_id == run_id)
        ) == 0


def test_team_completion_uses_database_time_at_commit_boundary(
    state_race_environment,
    monkeypatch,
):
    from app.runtime import runner_gateway_service as service_module

    factory, run_id, _agent_snapshot, _agent_claims = state_race_environment
    with factory() as clock_session:
        created_at = clock_session.scalar(select(func.clock_timestamp()))
    snapshot = _team_snapshot(
        run_id,
        created_at=created_at,
        timeout_seconds=5,
    )
    claims = _claims(snapshot)
    application_time = created_at.replace(microsecond=0)
    deadline = created_at + timedelta(seconds=5)
    slept_past_database_deadline = []

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return application_time

    monkeypatch.setattr(service_module, "datetime", FrozenDatetime)

    def sleep_past_deadline(session):
        before_sleep = session.scalar(select(func.clock_timestamp()))
        assert before_sleep < deadline
        session.execute(
            text("SELECT pg_sleep(:delay_seconds)"),
            {
                "delay_seconds": (
                    deadline - before_sleep
                ).total_seconds() + 0.25
            },
        )
        slept_past_database_deadline.append(True)

    with factory() as session:
        event.listen(session, "before_commit", sleep_past_deadline)
        try:
            with pytest.raises(RunnerGatewayError) as captured:
                _service(session, snapshot).complete(
                    run_id,
                    CompletionRequest(
                        status="completed",
                        final_assistant_content="late database-time Team result",
                    ),
                    claims,
                    f"completion:{run_id}:database-time",
                )
        finally:
            event.remove(session, "before_commit", sleep_past_deadline)

    assert slept_past_database_deadline == [True]
    assert captured.value.status_code == 409
    assert captured.value.code == "sandbox_timeout"
    with factory() as verification:
        assert verification.get(AgentRun, run_id).status == "running"
        assert verification.scalar(
            select(func.count())
            .select_from(RuntimeRunnerRequest)
            .where(RuntimeRunnerRequest.run_id == run_id)
        ) == 0
        assert verification.scalar(
            select(func.count())
            .select_from(RunEvent)
            .where(RunEvent.run_id == run_id)
        ) == 0
        assert verification.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.run_id == run_id)
        ) == 0


def test_team_tool_success_uses_database_time_at_commit_boundary(
    state_race_environment,
    monkeypatch,
):
    factory, run_id, _agent_snapshot, _agent_claims = state_race_environment
    with factory() as clock_session:
        created_at = clock_session.scalar(select(func.clock_timestamp()))
    snapshot = _team_snapshot(
        run_id,
        created_at=created_at,
        timeout_seconds=5,
    )
    deadline = created_at + timedelta(seconds=5)
    tool_id = "system.get_current_time"
    tool_store = ToolStore(factory)
    tool_definition = next(
        definition
        for definition in BUILTIN_TOOL_DEFINITIONS
        if definition["tool_id"] == tool_id
    )
    tool_store.upsert_builtin(
        {
            **tool_definition,
            "requires_approval": True,
            "risk_level": "high",
        }
    )

    with factory.begin() as setup:
        run = setup.get(AgentRun, run_id)
        run.actor_type = "team"
        run.actor_id = snapshot.payload.actor.id
        run.actor_version_id = snapshot.payload.actor.version_id
        run.status = "queued"
        invocation = ToolInvocation(
            run_id=run_id,
            tool_call_id="postgres-team-deadline-call",
            tool_id=tool_id,
            tool_version=tool_definition["version"],
            status="waiting_approval",
            arguments_summary={},
        )
        setup.add(invocation)
        setup.flush()
        setup.add_all(
            [
                Approval(
                    run_id=run_id,
                    invocation_id=invocation.id,
                    tool_id=tool_id,
                    tool_version=tool_definition["version"],
                    unit_id=snapshot.payload.unit_id,
                    project_id=snapshot.payload.project_id,
                    requester_id=snapshot.payload.user_id,
                    requester_roles=["user"],
                    assignee_role="project_admin",
                    risk_level="high",
                    arguments_summary={},
                    arguments_digest=arguments_digest({}),
                    status="approved",
                    expires_at=created_at + timedelta(hours=1),
                ),
                RuntimeExecutionSnapshot(
                    snapshot_id=snapshot.snapshot_id,
                    run_id=run_id,
                    digest=snapshot.digest,
                    payload=snapshot.payload.model_dump(mode="json"),
                    created_at=snapshot.created_at,
                    expires_at=snapshot.expires_at,
                ),
            ]
        )
        approval_id = setup.scalar(
            select(Approval.id).where(Approval.invocation_id == invocation.id)
        )
        invocation_id = invocation.id

    application_time = created_at
    external_calls = []
    original_executor = BUILTIN_EXECUTORS[tool_id]

    def record_external_call(arguments, execution_context, clock):
        external_calls.append(execution_context.run_id)
        return original_executor(arguments, execution_context, clock)

    monkeypatch.setitem(BUILTIN_EXECUTORS, tool_id, record_external_call)
    terminal_success_flushed = []
    slept_past_database_deadline = []

    def observe_terminal_success_flush(session, _flush_context):
        if not terminal_success_flushed and any(
            isinstance(item, RunEvent) and item.event_type == "tool.completed"
            for item in session.new
        ):
            terminal_success_flushed.append(True)

    def sleep_past_deadline(session):
        if (
            session.in_nested_transaction()
            or not terminal_success_flushed
            or slept_past_database_deadline
        ):
            return
        before_sleep = session.scalar(select(func.clock_timestamp()))
        assert before_sleep < deadline
        session.execute(
            text("SELECT pg_sleep(:delay_seconds)"),
            {
                "delay_seconds": (
                    deadline - before_sleep
                ).total_seconds() + 0.25
            },
        )
        slept_past_database_deadline.append(True)

    with factory() as session:
        gateway = ToolGateway(
            tool_store=tool_store,
            repository=ConversationRepository(session),
            clock=lambda: application_time,
        )
        event.listen(session, "after_flush", observe_terminal_success_flush)
        event.listen(session, "before_commit", sleep_past_deadline)
        try:
            with pytest.raises(ToolRuntimeError) as captured:
                gateway.execute_approved(
                    approval_id,
                    ToolExecutionContext(
                        unit_id=snapshot.payload.unit_id,
                        run_id=run_id,
                        conversation_id=f"unused:{run_id}",
                        project_id=snapshot.payload.project_id,
                        user_id=snapshot.payload.user_id,
                        actor_roles=("user",),
                    ),
                )
        finally:
            event.remove(session, "after_flush", observe_terminal_success_flush)
            event.remove(session, "before_commit", sleep_past_deadline)

    assert terminal_success_flushed == [True]
    assert slept_past_database_deadline == [True]
    assert captured.value.code == "sandbox_timeout"
    assert external_calls == [run_id]
    with factory() as verification:
        invocation = verification.get(ToolInvocation, invocation_id)
        assert invocation.status == "started"
        assert invocation.result_summary is None
        assert invocation.completed_at is None
        assert [
            item.event_type
            for item in verification.scalars(
                select(RunEvent)
                .where(RunEvent.run_id == run_id)
                .order_by(RunEvent.sequence)
            )
        ] == ["tool.started"]
        assert [
            item.action
            for item in verification.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.run_id == run_id,
                    AuditEvent.action.like("tool.invoke.%"),
                )
                .order_by(AuditEvent.occurred_at, AuditEvent.id)
            )
        ] == ["tool.invoke.started"]
