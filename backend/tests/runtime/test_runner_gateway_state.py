import hashlib
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.artifacts.service import ArtifactNotFoundError
from app.conversations.models import AgentRun, Conversation, Message
from app.conversations.repository import ConversationRepository
from app.db.base import Base
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
from app.runtime.runner_gateway_auth import (
    RunnerGatewayError,
    runner_gateway_error_handler,
)
from app.runtime.runner_gateway_router import create_router


def build_snapshot(digest_override=None):
    payload = ExecutionSnapshotPayload(
        snapshot_id="snapshot-1",
        run_id="run-1",
        unit_id="unit-1",
        project_id="project-1",
        user_id="user-1",
        actor=PublishedAgentSnapshot(
            id="agent-1",
            name="Agent",
            description="",
            runtime_form="common",
            language="zh-CN",
            system_prompt="",
            context_prompt="",
            approval_policy="never",
        ),
        model=SnapshotModelSelection(provider_id="provider-1", model="model-1"),
        messages=(),
        limits=SnapshotRuntimeLimits(snapshot_max_bytes=1048576),
        created_at=datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
    )
    digest = hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest()
    return StoredExecutionSnapshot(
        snapshot_id=payload.snapshot_id,
        run_id=payload.run_id,
        digest=digest_override or digest,
        payload=payload,
        created_at=payload.created_at,
        expires_at=None,
    )


def build_team_snapshot(*, timeout_seconds=60):
    base = build_snapshot()
    member = SnapshotTeamMember(
        agent_id="member-1",
        role="member",
        responsibility="inspect",
    )
    supervisor = member.model_copy(
        update={
            "agent_id": "supervisor",
            "role": "supervisor",
            "responsibility": "coordinate",
        }
    )
    actor = PublishedTeamSnapshot(
        id="team-1",
        version_id="team-version-1",
        version=1,
        definition_digest="d" * 64,
        supervisor=supervisor,
        members=(member,),
        max_steps=2,
        max_parallel_members=1,
        timeout_seconds=timeout_seconds,
        failure_strategy="fail_fast",
        name="Team",
        description="",
        runtime_form="common",
        language="zh-CN",
        system_prompt="",
        context_prompt="",
        approval_policy="never",
    )
    payload = base.payload.model_copy(
        update={"schema_version": "5", "actor": actor}
    )
    return base.model_copy(
        update={
            "digest": hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest(),
            "payload": payload,
        }
    )


class FakeTokenService:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def verify(self, _token, run_id, action):
        return RunTokenClaims(
            iss="iap-api",
            aud="iap-runner-gateway",
            jti="token-1",
            run_id=run_id,
            unit_id="unit-1",
            project_id="project-1",
            snapshot_id=self.snapshot.snapshot_id,
            snapshot_digest=self.snapshot.digest,
            actions=(action,),
            iat=1,
            nbf=1,
            exp=9999999999,
        )


class FakeSnapshotService:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def get(self, snapshot_id):
        return self.snapshot if snapshot_id == self.snapshot.snapshot_id else None


class FakeArtifactService:
    def __init__(self, artifacts=None):
        self.artifacts = artifacts or {}

    def get_for_run(self, run_id, artifact_id):
        artifact = self.artifacts.get(artifact_id)
        if artifact is None or artifact.run_id != run_id:
            raise ArtifactNotFoundError(artifact_id)
        return artifact


def build_client(snapshot=None, *, event_payload_max_bytes=65536, artifacts=None):
    snapshot = snapshot or build_snapshot()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    conversation = Conversation(
        id="conversation-1",
        unit_id="unit-1",
        project_id="project-1",
        owner_id="user-1",
        title="Runner gateway test",
    )
    message = Message(
        id="message-1",
        conversation_id=conversation.id,
        sequence=1,
        role="user",
        content="test",
    )
    run = AgentRun(
        id="run-1",
        conversation_id=conversation.id,
        trigger_message_id=message.id,
        actor_type=snapshot.payload.actor.kind,
        actor_id=snapshot.payload.actor.id,
        status="running",
    )
    session.add_all([conversation, message, run])
    session.commit()

    checkpoint_store = CheckpointStore(session)
    repository = ConversationRepository(session)
    app = FastAPI()
    app.add_exception_handler(RunnerGatewayError, runner_gateway_error_handler)
    token_service = FakeTokenService(snapshot)
    app.include_router(
        create_router(
            token_service_dependency=lambda: token_service,
            snapshot_service_dependency=lambda: FakeSnapshotService(snapshot),
            checkpoint_store_dependency=lambda: checkpoint_store,
            conversation_repository_dependency=lambda: repository,
            artifact_service_dependency=lambda: FakeArtifactService(artifacts),
            event_payload_max_bytes=event_payload_max_bytes,
        ),
        prefix="/internal/runner",
    )
    return TestClient(app), repository, checkpoint_store, token_service


def bearer():
    return {"Authorization": "Bearer valid"}


def idempotent(key):
    return {**bearer(), "Idempotency-Key": key}


def test_checkpoint_restore_rejects_other_snapshot_digest():
    client, _repository, _store, token_service = build_client(build_snapshot("a" * 64))
    response = client.put(
        "/internal/runner/runs/run-1/checkpoints/step-1",
        headers=idempotent("checkpoint-1"),
        json={"state": {"status": "running"}},
    )
    assert response.status_code == 200

    token_service.snapshot = build_snapshot("b" * 64)
    response = client.get(
        "/internal/runner/runs/run-1/checkpoints/latest",
        headers=bearer(),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "checkpoint_snapshot_mismatch"


def test_checkpoint_round_trip_is_bound_to_token_snapshot_digest():
    snapshot = build_snapshot()
    client, _repository, _store, _token_service = build_client(snapshot)

    saved = client.put(
        "/internal/runner/runs/run-1/checkpoints/step-1",
        headers=idempotent("checkpoint-1"),
        json={"state": {"status": "running"}},
    )
    restored = client.get(
        "/internal/runner/runs/run-1/checkpoints/latest",
        headers=bearer(),
    )

    assert saved.status_code == 200
    assert restored.status_code == 200
    assert restored.json() == {
        "checkpoint_key": "step-1",
        "snapshot_digest": snapshot.digest,
        "state": {"status": "running"},
    }


def test_generic_checkpoint_api_rejects_gateway_reserved_namespace():
    client, _repository, store, _token_service = build_client()

    response = client.put(
        "/internal/runner/runs/run-1/checkpoints/__runner_gateway__:artifact-capability",
        headers=idempotent("checkpoint-reserved-spoof"),
        json={"state": {"capability": "attacker-controlled"}},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "checkpoint_namespace_reserved"
    assert store.load_latest("run-1") is None


def test_duplicate_checkpoint_idempotency_key_returns_stored_response():
    client, _repository, store, _token_service = build_client()
    request = {"state": {"status": "running"}}

    first = client.put(
        "/internal/runner/runs/run-1/checkpoints/step-1",
        headers=idempotent("checkpoint-1"),
        json=request,
    )
    second = client.put(
        "/internal/runner/runs/run-1/checkpoints/step-1",
        headers=idempotent("checkpoint-1"),
        json=request,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    assert store.load_latest("run-1") == {"status": "running"}


def test_reused_checkpoint_idempotency_key_with_different_state_is_conflict():
    client, _repository, store, _token_service = build_client()
    assert client.put(
        "/internal/runner/runs/run-1/checkpoints/step-1",
        headers=idempotent("checkpoint-1"),
        json={"state": {"status": "running"}},
    ).status_code == 200

    response = client.put(
        "/internal/runner/runs/run-1/checkpoints/step-1",
        headers=idempotent("checkpoint-1"),
        json={"state": {"status": "completed"}},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "idempotency_conflict"
    assert store.load_latest("run-1") == {"status": "running"}


def test_duplicate_event_idempotency_key_creates_one_event():
    client, repository, _store, _token_service = build_client()
    request = {
        "sequence": 1,
        "event_type": "model.delta",
        "payload": {"text": "hello"},
    }

    first = client.post(
        "/internal/runner/runs/run-1/events",
        headers=idempotent("evt-1"),
        json=request,
    )
    second = client.post(
        "/internal/runner/runs/run-1/events",
        headers=idempotent("evt-1"),
        json=request,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    assert len(repository.list_events("run-1", 0)) == 1


def test_terminal_run_rejects_new_checkpoint_but_preserves_exact_replay():
    client, repository, _store, _token_service = build_client()
    checkpoint_request = {"state": {"status": "running"}}
    first = client.put(
        "/internal/runner/runs/run-1/checkpoints/step-1",
        headers=idempotent("checkpoint-1"),
        json=checkpoint_request,
    )
    completion = client.post(
        "/internal/runner/runs/run-1/completion",
        headers=idempotent("completion:timeout"),
        json={"status": "failed", "error_code": "sandbox_timeout"},
    )
    checkpoint_count = repository.session.query(RuntimeCheckpoint).count()
    request_count = repository.session.query(RuntimeRunnerRequest).count()

    replay = client.put(
        "/internal/runner/runs/run-1/checkpoints/step-1",
        headers=idempotent("checkpoint-1"),
        json=checkpoint_request,
    )
    rejected = client.put(
        "/internal/runner/runs/run-1/checkpoints/step-2",
        headers=idempotent("checkpoint-2"),
        json={"state": {"status": "late"}},
    )

    assert first.status_code == 200
    assert completion.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "run_not_active"
    assert repository.session.query(RuntimeCheckpoint).count() == checkpoint_count
    assert repository.session.query(RuntimeRunnerRequest).count() == request_count


def test_terminal_run_rejects_new_event_but_preserves_exact_replay():
    client, repository, _store, _token_service = build_client()
    event_request = {
        "sequence": 1,
        "event_type": "runner.started",
        "payload": {"status": "running"},
    }
    first = client.post(
        "/internal/runner/runs/run-1/events",
        headers=idempotent("event-1"),
        json=event_request,
    )
    completion = client.post(
        "/internal/runner/runs/run-1/completion",
        headers=idempotent("completion:timeout"),
        json={"status": "failed", "error_code": "sandbox_timeout"},
    )
    event_count = len(repository.list_events("run-1", 0))
    request_count = repository.session.query(RuntimeRunnerRequest).count()

    replay = client.post(
        "/internal/runner/runs/run-1/events",
        headers=idempotent("event-1"),
        json=event_request,
    )
    rejected = client.post(
        "/internal/runner/runs/run-1/events",
        headers=idempotent("event-2"),
        json={
            "sequence": 2,
            "event_type": "model.delta",
            "payload": {"text": "late"},
        },
    )

    assert first.status_code == 200
    assert completion.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "run_not_active"
    assert len(repository.list_events("run-1", 0)) == event_count
    assert repository.session.query(RuntimeRunnerRequest).count() == request_count


def test_reused_idempotency_key_with_different_event_is_conflict():
    client, repository, _store, _token_service = build_client()
    first = {
        "sequence": 1,
        "event_type": "model.delta",
        "payload": {"text": "first"},
    }
    changed = {**first, "payload": {"text": "changed"}}

    assert client.post(
        "/internal/runner/runs/run-1/events",
        headers=idempotent("evt-1"),
        json=first,
    ).status_code == 200
    response = client.post(
        "/internal/runner/runs/run-1/events",
        headers=idempotent("evt-1"),
        json=changed,
    )

    assert response.status_code == 409
    assert response.json()["code"] == "idempotency_conflict"
    assert len(repository.list_events("run-1", 0)) == 1


def test_event_sequence_gap_is_rejected():
    client, repository, _store, _token_service = build_client()

    response = client.post(
        "/internal/runner/runs/run-1/events",
        headers=idempotent("evt-2"),
        json={
            "sequence": 2,
            "event_type": "model.delta",
            "payload": {"text": "late"},
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "event_sequence_invalid"
    assert repository.list_events("run-1", 0) == []


def test_event_payload_over_limit_is_rejected():
    client, repository, _store, _token_service = build_client(event_payload_max_bytes=32)

    response = client.post(
        "/internal/runner/runs/run-1/events",
        headers=idempotent("evt-1"),
        json={
            "sequence": 1,
            "event_type": "model.delta",
            "payload": {"text": "x" * 64},
        },
    )

    assert response.status_code == 413
    assert response.json()["code"] == "event_payload_too_large"
    assert repository.list_events("run-1", 0) == []


def test_completion_commits_final_message_status_and_artifact_references_once():
    from types import SimpleNamespace

    artifact = SimpleNamespace(id="artifact-1", run_id="run-1")
    client, repository, _store, _token_service = build_client(
        artifacts={artifact.id: artifact}
    )
    request = {
        "status": "completed",
        "final_assistant_content": "任务已完成",
        "checkpoint_key": "langgraph",
        "artifact_refs": [artifact.id],
    }

    first = client.post(
        "/internal/runner/runs/run-1/completion",
        headers=idempotent("completion:final"),
        json=request,
    )
    second = client.post(
        "/internal/runner/runs/run-1/completion",
        headers=idempotent("completion:final"),
        json=request,
    )

    assert first.status_code == 200
    assert second.json() == first.json()
    assert first.json() == {
        "run_id": "run-1",
        "status": "completed",
        "checkpoint_key": "langgraph",
        "artifact_refs": ["artifact-1"],
    }
    assert repository.get_run_by_id("run-1").status == "completed"
    messages = list(
        repository.session.scalars(
            select(Message)
            .where(Message.conversation_id == "conversation-1")
            .order_by(Message.sequence)
        )
    )
    assert [(message.role, message.content) for message in messages] == [
        ("user", "test"),
        ("assistant", "任务已完成"),
    ]
    assert [event.event_type for event in repository.list_events("run-1", 0)] == [
        "runner.completion",
        "run.status",
    ]


def test_team_success_before_deadline_commits_guarded_status_and_result(
    monkeypatch,
):
    from app.runtime import runner_gateway_service as service_module

    snapshot = build_team_snapshot(timeout_seconds=60)

    class BeforeDeadlineDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return snapshot.created_at + timedelta(seconds=30)

    monkeypatch.setattr(service_module, "datetime", BeforeDeadlineDatetime)
    client, repository, _store, _token_service = build_client(snapshot)

    response = client.post(
        "/internal/runner/runs/run-1/completion",
        headers=idempotent("completion:team-before-deadline"),
        json={
            "status": "completed",
            "final_assistant_content": "on-time Team result",
        },
    )

    assert response.status_code == 200
    assert repository.get_run_by_id("run-1").status == "completed"
    assert [event.event_type for event in repository.list_events("run-1", 0)] == [
        "runner.completion",
        "run.status",
    ]
    assert repository.session.scalar(
        select(func.count())
        .select_from(RuntimeRunnerRequest)
        .where(RuntimeRunnerRequest.run_id == "run-1")
    ) == 1
    assert [
        (message.role, message.content)
        for message in repository.session.scalars(
            select(Message)
            .where(Message.conversation_id == "conversation-1")
            .order_by(Message.sequence)
        )
    ] == [
        ("user", "test"),
        ("assistant", "on-time Team result"),
    ]


def test_expired_team_success_is_rejected_at_locked_gateway_boundary(monkeypatch):
    from app.runtime import runner_gateway_service as service_module

    snapshot = build_team_snapshot(timeout_seconds=60)

    class ExpiredDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return snapshot.created_at + timedelta(seconds=61)

    monkeypatch.setattr(service_module, "datetime", ExpiredDatetime)
    client, repository, _store, _token_service = build_client(snapshot)

    response = client.post(
        "/internal/runner/runs/run-1/completion",
        headers=idempotent("completion:late-team-success"),
        json={
            "status": "completed",
            "final_assistant_content": "This Team result crossed its deadline.",
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "sandbox_timeout"
    assert repository.get_run_by_id("run-1").status == "running"
    assert repository.list_events("run-1", 0) == []
    assert list(
        repository.session.scalars(
            select(Message).where(Message.conversation_id == "conversation-1")
        )
    )[0].role == "user"


def test_team_success_crossing_deadline_during_completion_writes_rolls_back(
    monkeypatch,
):
    from app.runtime import runner_gateway_service as service_module

    snapshot = build_team_snapshot(timeout_seconds=60)
    current_time = [snapshot.created_at + timedelta(seconds=59)]

    class MutableDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return current_time[0]

    original_add = ConversationRepository.add_assistant_message

    def add_after_deadline(repository, run_id, content):
        message = original_add(repository, run_id, content)
        current_time[0] = snapshot.created_at + timedelta(seconds=61)
        return message

    monkeypatch.setattr(service_module, "datetime", MutableDatetime)
    monkeypatch.setattr(
        ConversationRepository,
        "add_assistant_message",
        add_after_deadline,
    )
    client, repository, _store, _token_service = build_client(snapshot)

    response = client.post(
        "/internal/runner/runs/run-1/completion",
        headers=idempotent("completion:crossed-during-writes"),
        json={
            "status": "completed",
            "final_assistant_content": "late Team result",
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "sandbox_timeout"
    assert repository.get_run_by_id("run-1").status == "running"
    assert repository.list_events("run-1", 0) == []
    assert [
        message.role
        for message in repository.session.scalars(
            select(Message).where(Message.conversation_id == "conversation-1")
        )
    ] == ["user"]


def test_team_success_crossing_deadline_immediately_before_commit_rolls_back(
    monkeypatch,
):
    from app.runtime import runner_gateway_service as service_module

    snapshot = build_team_snapshot(timeout_seconds=60)
    current_time = [snapshot.created_at + timedelta(seconds=59)]

    class MutableDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return current_time[0]

    monkeypatch.setattr(service_module, "datetime", MutableDatetime)
    client, repository, _store, _token_service = build_client(snapshot)
    crossed_at_commit = []

    def cross_before_completion_commit(_session):
        current_time[0] = snapshot.created_at + timedelta(seconds=61)
        crossed_at_commit.append(True)

    event.listen(
        repository.session,
        "before_commit",
        cross_before_completion_commit,
    )
    try:
        response = client.post(
            "/internal/runner/runs/run-1/completion",
            headers=idempotent("completion:crossed-before-commit"),
            json={
                "status": "completed",
                "final_assistant_content": "late Team result",
            },
        )
    finally:
        event.remove(
            repository.session,
            "before_commit",
            cross_before_completion_commit,
        )

    assert crossed_at_commit == [True]
    assert response.status_code == 409
    assert response.json()["code"] == "sandbox_timeout"
    assert repository.get_run_by_id("run-1").status == "running"
    assert repository.list_events("run-1", 0) == []
    assert repository.session.scalar(
        select(func.count())
        .select_from(RuntimeRunnerRequest)
        .where(RuntimeRunnerRequest.run_id == "run-1")
    ) == 0
    assert [
        message.role
        for message in repository.session.scalars(
            select(Message).where(Message.conversation_id == "conversation-1")
        )
    ] == ["user"]


def test_completion_rejects_artifact_from_another_run():
    from types import SimpleNamespace

    artifact = SimpleNamespace(id="artifact-2", run_id="run-2")
    client, repository, _store, _token_service = build_client(
        artifacts={artifact.id: artifact}
    )

    response = client.post(
        "/internal/runner/runs/run-1/completion",
        headers=idempotent("completion:wrong-artifact"),
        json={"status": "completed", "artifact_refs": [artifact.id]},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "artifact_not_found"
    assert repository.get_run_by_id("run-1").status == "running"


def test_interrupted_completion_preserves_waiting_approval_state():
    client, repository, _store, _token_service = build_client()

    response = client.post(
        "/internal/runner/runs/run-1/completion",
        headers=idempotent("completion:approval"),
        json={
            "status": "interrupted",
            "error_code": "approval_required",
            "approval_id": "approval-1",
            "checkpoint_key": "approval-approval-1",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "interrupted"
    assert repository.get_run_by_id("run-1").status == "waiting_approval"


def test_completed_completion_cannot_overwrite_waiting_approval_state():
    client, repository, _store, _token_service = build_client()
    run = repository.get_run_by_id("run-1")
    run.status = "waiting_approval"
    repository.session.commit()

    response = client.post(
        "/internal/runner/runs/run-1/completion",
        headers=idempotent("completion:late-after-approval"),
        json={
            "status": "completed",
            "final_assistant_content": "This completion arrived too late.",
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "completion_conflicts_with_approval"
    assert repository.get_run_by_id("run-1").status == "waiting_approval"
    assert repository.list_events("run-1", 0) == []
    messages = list(
        repository.session.scalars(
            select(Message)
            .where(Message.conversation_id == "conversation-1")
            .order_by(Message.sequence)
        )
    )
    assert [(message.role, message.content) for message in messages] == [
        ("user", "test"),
    ]
