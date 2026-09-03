import hashlib
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.conversations.models import AgentRun, Conversation, Message, RunEvent
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


def _team_snapshot(run_id: str) -> StoredExecutionSnapshot:
    created_at = datetime(2026, 9, 4, 10, 0, tzinfo=UTC)
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
        timeout_seconds=60,
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
            delete(RuntimeRunnerRequest).where(
                RuntimeRunnerRequest.run_id == run_id
            )
        )
        cleanup.execute(
            delete(RuntimeCheckpoint).where(RuntimeCheckpoint.run_id == run_id)
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
