import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.collaboration.schemas import TeamCreateRequest, TeamDraft, TeamDraftUpdate, TeamMemberDraft
from app.collaboration.repository import TeamNotFoundError
from app.collaboration.service import TeamService
from app.conversations.models import AgentRun
from app.conversations.repository import ConversationRepository
from app.conversations.schemas import ConversationCreate, MessageCreate
from app.conversations.service import AgentSelectionError, ConversationService
from app.core.request_context import RequestContext
from app.db.base import Base
from app.runtime.execution_contract import RunExecutionRequest
from app.runtime.artifact_backend import ArtifactBackend
from app.runtime.execution_snapshot import (
    ExecutionSnapshotPayload, PublishedTeamSnapshot, SnapshotMessage,
    SnapshotModelSelection, SnapshotRuntimeLimits, SnapshotTeamMember,
    canonical_snapshot_bytes,
)
from app.runtime.runner_gateway_schemas import SnapshotResponse
from app.runtime.sandbox_runtime import SandboxRuntime


class NoopDispatcher:
    def __init__(self):
        self.run_ids = []

    def dispatch(self, run_id):
        self.run_ids.append(run_id)


def context(*, project="project-1", roles=frozenset({"project_admin"})):
    return RequestContext(user_id="user-1", unit_id="unit-1", project_id=project, roles=roles)


def team_draft():
    member = lambda agent_id, responsibility, digest: TeamMemberDraft(  # noqa: E731
        agent_id=agent_id, responsibility=responsibility,
        agent_definition_digest=digest * 64,
    )
    return TeamDraft(
        supervisor=member("supervisor", "统筹与汇总", "a"),
        members=[member("forecast", "洪水预报", "b"), member("review", "成果复核", "c")],
        max_steps=6, max_parallel_members=2, timeout_seconds=600,
    )


def test_published_team_run_records_exact_version_and_rejects_missing_run_permission():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        team_service = TeamService(session)
        admin = context()
        team = team_service.create(admin, TeamCreateRequest(name="北江联合研判"))
        team_service.save_draft(admin, team.id, TeamDraftUpdate(revision=1, draft=team_draft()))
        version = team_service.publish(admin, team.id)
        team_service.set_enabled(admin, team.id, True)
        with pytest.raises(TeamNotFoundError):
            team_service.get(context(project="project-2"), team.id)
        dispatcher = NoopDispatcher()
        conversation_service = ConversationService(
            ConversationRepository(session), dispatcher, team_service=team_service,
        )
        conversation = conversation_service.create_conversation(admin, ConversationCreate(title="联合研判"))

        accepted = conversation_service.create_message(
            admin, conversation.id,
            MessageCreate(content="联合研判未来洪峰", actor_type="team", actor_id=team.id),
        )

        stored = session.get(AgentRun, accepted.run.id)
        assert stored is not None
        assert stored.actor_version_id == version.id
        assert dispatcher.run_ids == [stored.id]
        with pytest.raises(AgentSelectionError, match="collaboration.run"):
            conversation_service.create_message(
                context(roles=frozenset({"user"})), conversation.id,
                MessageCreate(content="越权调用", actor_type="team", actor_id=team.id),
            )


def team_snapshot():
    actor = PublishedTeamSnapshot(
        id="team-1", version_id="version-2", version=2, definition_digest="d" * 64,
        supervisor=SnapshotTeamMember(agent_id="supervisor", role="supervisor", responsibility="统筹与汇总"),
        members=(
            SnapshotTeamMember(agent_id="forecast", role="member", responsibility="洪水预报"),
            SnapshotTeamMember(agent_id="review", role="member", responsibility="成果复核"),
        ),
        max_steps=6, max_parallel_members=2, timeout_seconds=600,
        failure_strategy="fail_fast", name="北江联合研判", description="",
        runtime_form="common", language="zh-CN", system_prompt="统筹", context_prompt="",
        approval_policy="never",
    )
    payload = ExecutionSnapshotPayload(
        schema_version="4", snapshot_id="snapshot-1", run_id="run-1",
        unit_id="unit-1", project_id="project-1", user_id="user-1", actor=actor,
        model=SnapshotModelSelection(provider_id="provider-1", model="model-1"),
        messages=(SnapshotMessage(id="message-1", sequence=1, role="user", content="联合研判", created_at=datetime(2026, 9, 1, tzinfo=UTC)),),
        limits=SnapshotRuntimeLimits(snapshot_max_bytes=1_048_576, max_subagents=3),
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    return SnapshotResponse(
        snapshot_id=payload.snapshot_id, run_id=payload.run_id,
        digest=hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest(), payload=payload,
    )


class RuntimeGateway:
    def __init__(self, snapshot):
        self.snapshot = snapshot; self.events = []; self.completions = []

    def get_snapshot(self): return self.snapshot
    def get_latest_checkpoint(self): return None
    def save_checkpoint(self, checkpoint_key, state, idempotency_key): return {"checkpoint_key": checkpoint_key, "state": state}
    def append_event(self, **request): self.events.append(request); return request
    def complete(self, request, idempotency_key): self.completions.append(request); return request
    def list_artifacts(self): return []


class MemberFactory:
    def __init__(self): self.agent_ids = []
    def build(self, snapshot, **kwargs): self.agent_ids.append(snapshot.agent_id); return snapshot.agent_id


class MemberAdapter:
    def __init__(self, graph, checkpoint_store=None): self.graph = graph
    def invoke(self, state, metadata): return SimpleNamespace(status="completed", content=f"{self.graph} 完成")


def test_published_team_runtime_completes_two_member_tasks_and_one_synthesis():
    snapshot = team_snapshot(); gateway = RuntimeGateway(snapshot); factory = MemberFactory()
    request = RunExecutionRequest(
        run_id="run-1", agent_version="version-2", checkpoint_key="initial",
        deadline_at=datetime.now(UTC) + timedelta(minutes=5), snapshot_id="snapshot-1",
        snapshot_digest=snapshot.digest, gateway_url="http://runner-gateway/internal", run_token="token",
    )

    result = SandboxRuntime(gateway, agent_factory=factory, runtime_adapter_type=MemberAdapter).execute(request)

    event_types = [event["event_type"] for event in gateway.events]
    assert result.status == "completed"
    assert event_types.count("team.task.completed") == 2
    assert event_types.count("team.synthesis.completed") == 1
    assert factory.agent_ids == ["forecast", "review", "supervisor"]
    assert all(event["payload"].get("version_id") == "version-2" for event in gateway.events if event["event_type"].startswith("team."))


def test_team_member_artifacts_send_version_member_and_task_provenance():
    class ArtifactGateway:
        def __init__(self): self.request = None
        def list_artifacts(self): return []
        def create_artifact(self, **request):
            self.request = request
            return {"path": request["path"], "artifact_id": "artifact-1", "size_bytes": len(request["data"]), "sha256": request["sha256"], "content_type": request["content_type"]}

    gateway = ArtifactGateway()
    backend = ArtifactBackend(gateway, provenance={
        "team_version_id": "version-2", "member_agent_id": "forecast", "task_id": "member-1",
    })
    result = backend.write("/artifacts/forecast.txt", "完成")

    assert result.error is None
    assert gateway.request["provenance"] == {
        "team_version_id": "version-2", "member_agent_id": "forecast", "task_id": "member-1",
    }
