import hashlib
import json
import os
import secrets
import socket
import time
from threading import Thread
from uuid import uuid4
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
from app.identity.catalogue import ROLE_PERMISSION_CODES
from app.identity.schemas import AuthorizationContext, PermissionGrant
from app.runtime.execution_contract import RunExecutionRequest
from app.runtime.artifact_backend import ArtifactBackend
from app.runtime.execution_snapshot import (
    ExecutionSnapshotPayload, PublishedTeamSnapshot, SnapshotMessage,
    SnapshotModelSelection, SnapshotRuntimeLimits, SnapshotTeamMember,
    canonical_snapshot_bytes,
)
from app.runtime.runner_gateway_client import RunnerGatewayBusinessError
from app.runtime.runner_gateway_schemas import SnapshotResponse
from app.runtime.sandbox_runtime import SandboxRuntime


class NoopDispatcher:
    def __init__(self):
        self.run_ids = []

    def dispatch(self, run_id):
        self.run_ids.append(run_id)


class LocalTeamAgentService:
    def __init__(self):
        self.agents = {
            agent_id: SimpleNamespace(
                id=agent_id,
                name=agent_id,
                description="",
                runtime_form="common",
                language="zh-CN",
                provider_id="provider-1",
                model="model-1",
                system_prompt="",
                context_prompt="",
                approval_policy="never",
                skill_names=[],
                tool_ids=[],
                knowledge_source_ids=[],
                enabled=True,
                availability_scope="project",
                unit_id="unit-1",
                project_id="project-1",
                allowed_project_ids=[],
            )
            for agent_id in ("supervisor", "forecast", "review")
        }
        self.tool_service = SimpleNamespace(
            resolve_bindable=lambda tool_ids: [],
            resolve_knowledge_sources=lambda tool_ids: [],
        )
        self.skill_service = SimpleNamespace()

    def get(self, agent_id):
        return self.agents[agent_id]

    def get_available(self, agent_id, *, unit_id, project_id):
        agent = self.get(agent_id)
        if agent.unit_id != unit_id or agent.project_id != project_id:
            raise KeyError(agent_id)
        return agent


class LocalTeamProviderService:
    def get(self, provider_id):
        if provider_id != "provider-1":
            raise KeyError(provider_id)
        return SimpleNamespace(
            id=provider_id,
            configured=True,
            enabled=True,
            models=[SimpleNamespace(id="model-1", enabled=True)],
        )


def context(*, project="project-1", roles=frozenset({"project_admin"}), user="user-1"):
    role_codes = tuple(sorted(roles))
    grants = tuple(
        PermissionGrant(code, "project", frozenset({project}), None)
        for role in role_codes
        for code in ROLE_PERMISSION_CODES.get("viewer" if role == "user" else role, ())
    )
    return RequestContext(
        user_id=user,
        unit_id="unit-1",
        project_id=project,
        roles=roles,
        authorization_context=AuthorizationContext(
            session_id="e2e-session",
            user_id=user,
            unit_id="unit-1",
            current_project_id=project,
            auth_method="dev_test",
            authorization_version=1,
            role_codes=role_codes,
            grants=grants,
        ),
    )


def team_draft():
    member = lambda agent_id, responsibility: TeamMemberDraft(  # noqa: E731
        agent_id=agent_id, responsibility=responsibility,
    )
    return TeamDraft(
        supervisor=member("supervisor", "统筹与汇总"),
        members=[member("forecast", "洪水预报"), member("review", "成果复核")],
        max_steps=6, max_parallel_members=2, timeout_seconds=600,
    )


def test_published_team_run_records_exact_version_and_rejects_missing_run_permission():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        agent_service = LocalTeamAgentService()
        team_service = TeamService(
            session,
            agent_service=agent_service,
            provider_service=LocalTeamProviderService(),
        )
        admin = context()
        team = team_service.create(admin, TeamCreateRequest(name="北江联合研判"))
        team_service.save_draft(admin, team.id, TeamDraftUpdate(revision=1, draft=team_draft()))
        version = team_service.publish(admin, team.id)
        team_service.set_enabled(admin, team.id, True)
        with pytest.raises(TeamNotFoundError):
            team_service.get(context(project="project-2"), team.id)
        dispatcher = NoopDispatcher()
        conversation_service = ConversationService(
            ConversationRepository(session),
            dispatcher,
            agent_service=agent_service,
            team_service=team_service,
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
    def get_latest_checkpoint(self): raise RunnerGatewayBusinessError("checkpoint_not_found")
    def save_checkpoint(self, checkpoint_key, state, idempotency_key): return {"checkpoint_key": checkpoint_key, "state": state}
    def register_artifact_capability(self, **request): return f"capability:{request['invocation_id']}"
    def append_event(self, **request): self.events.append(request); return request
    def complete(self, request, idempotency_key): self.completions.append(request); return request
    def list_artifacts(self): return []


class MemberFactory:
    def __init__(self): self.agent_ids = []
    def build(self, snapshot, **kwargs): self.agent_ids.append(snapshot.agent_id); return snapshot.agent_id


class MemberAdapter:
    def __init__(self, graph, checkpoint_store=None): self.graph = graph
    def invoke(self, state, metadata): return SimpleNamespace(status="completed", content=f"{self.graph} 完成", state={})


def test_published_team_runtime_completes_two_member_tasks_and_one_synthesis():
    snapshot = team_snapshot(); gateway = RuntimeGateway(snapshot); factory = MemberFactory()
    deadline_at = datetime.now(UTC) + timedelta(minutes=5)
    request = RunExecutionRequest(
        run_id="run-1", agent_version="version-2", checkpoint_key="initial",
        deadline_at=deadline_at, execution_deadline_at=deadline_at, snapshot_id="snapshot-1",
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
    backend = ArtifactBackend(
        gateway,
        provenance={
            "team_version_id": "version-2",
            "member_agent_id": "forecast",
            "task_id": "member-1",
            "invocation_id": "invocation-1",
        },
        capability="c" * 43,
    )
    result = backend.write("/artifacts/forecast.txt", "完成")

    assert result.error is None
    assert gateway.request["provenance"] == {
        "team_version_id": "version-2",
        "member_agent_id": "forecast",
        "task_id": "member-1",
        "invocation_id": "invocation-1",
    }
    assert gateway.request["capability"] == "c" * 43


@pytest.fixture
def live_control_runtime(tmp_path):
    """Production services with a real HTTP gateway and isolated in-memory secrets."""
    required = (
        "TEST_MODEL_PROVIDER_STATE_JSON", "TEST_OBJECT_STORAGE_ENDPOINT",
        "TEST_OBJECT_STORAGE_ACCESS_KEY", "TEST_OBJECT_STORAGE_SECRET_KEY",
    )
    if any(not os.getenv(name) for name in required):
        pytest.skip("requires explicit test model provider state and dedicated S3 test credentials")

    import boto3
    import uvicorn
    from fastapi import Depends, FastAPI
    from fastapi.exceptions import RequestValidationError
    from fastapi.exception_handlers import request_validation_exception_handler
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.agents.service import AgentService
    from app.agents.store import AgentStore
    from app.artifacts.service import ArtifactService
    from app.artifacts.storage import S3ObjectStorage
    from app.core.database import get_session
    from app.model_providers.service import ProviderService
    from app.model_providers.store import ProviderStore
    from app.runtime.model_gateway import OpenAICompatibleModelGateway
    from app.runtime.run_tokens import RunTokenService
    from app.runtime.runner_gateway_auth import RunnerGatewayError, runner_gateway_error_handler
    from app.runtime.runner_gateway_router import create_router
    from app.skills.service import SkillService

    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    provider_store = ProviderStore(sessions)
    try:
        provider_store.save(json.loads(os.environ["TEST_MODEL_PROVIDER_STATE_JSON"]))
    except Exception:
        engine.dispose()
        pytest.fail("Unable to seed isolated test provider configuration", pytrace=False)
    provider_service = ProviderService(provider_store)
    agent_service = AgentService(AgentStore(sessions), workspace_root=tmp_path / "agents", skill_service=SkillService(tmp_path / "skills"))
    storage_client = boto3.client(
        "s3", endpoint_url=os.environ["TEST_OBJECT_STORAGE_ENDPOINT"],
        aws_access_key_id=os.environ["TEST_OBJECT_STORAGE_ACCESS_KEY"],
        aws_secret_access_key=os.environ["TEST_OBJECT_STORAGE_SECRET_KEY"], region_name="us-east-1",
    )
    bucket = "iap-control-test-" + uuid4().hex
    storage = S3ObjectStorage(client=storage_client, bucket=bucket)
    signing_key = secrets.token_bytes(32)

    def session_dependency():
        with sessions() as session:
            yield session

    def token_dependency(session=Depends(get_session)):
        return RunTokenService(session, signing_key=signing_key)

    def artifact_dependency(session=Depends(get_session)):
        return ArtifactService(session, storage)

    app = FastAPI()
    app.dependency_overrides[get_session] = session_dependency
    gateway_errors = []

    async def record_gateway_error(request, error):
        gateway_errors.append(error.code)
        return await runner_gateway_error_handler(request, error)

    async def record_validation_error(request, error):
        gateway_errors.extend(str(item["loc"]) + ":" + item["type"] for item in error.errors())
        return await request_validation_exception_handler(request, error)

    app.add_exception_handler(RunnerGatewayError, record_gateway_error)
    app.add_exception_handler(RequestValidationError, record_validation_error)
    app.include_router(create_router(
        token_service_dependency=token_dependency,
        model_gateway_dependency=lambda: OpenAICompatibleModelGateway(provider_store),
        artifact_service_dependency=artifact_dependency,
    ), prefix="/internal/runner")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    try:
        storage_client.create_bucket(Bucket=bucket)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "real runner gateway did not start"
        yield SimpleNamespace(
            sessions=sessions, agents=agent_service, providers=provider_service, storage=storage,
            signing_key=signing_key, gateway_url=f"http://127.0.0.1:{listener.getsockname()[1]}/internal/runner",
            gateway_errors=gateway_errors,
        )
    finally:
        server.should_exit = True
        if thread.is_alive():
            thread.join(timeout=10)
        listener.close()
        try:
            for page in storage_client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
                for entry in page.get("Contents", []):
                    storage_client.delete_object(Bucket=bucket, Key=entry["Key"])
            storage_client.delete_bucket(Bucket=bucket)
        finally:
            storage_client.close()
            engine.dispose()
        assert not thread.is_alive(), "real runner gateway did not stop"


def test_team_simulation_dual_approval_execute_feedback_deviation_and_rollback(live_control_runtime):
    import httpx
    from sqlalchemy import select
    from app.agents.schemas import AgentCreateRequest
    from app.artifacts.models import ArtifactRecord
    from app.audit.models import AuditEvent
    from app.control.errors import ControlForbiddenError, ControlNotFoundError, ControlValidationError
    from app.control.executors import ControlExecutorRegistry
    from app.control.models import ControlExecutionAttempt, ControlFeedbackRecord
    from app.control.schemas import ControlCommandProposeRequest, ControlFeedbackRequest, ControlPolicyCreateRequest
    from app.control.service import ControlService
    from app.conversations.models import Conversation, Message, RunEvent
    from app.runtime.execution_snapshot import ExecutionSnapshotService
    from app.runtime.run_tokens import RunTokenService
    from app.runtime.runner_gateway_client import RunnerGatewayClient

    live = live_control_runtime
    requester = context(user="requester")
    reviewer_a = context(user="reviewer-a")
    reviewer_b = context(user="reviewer-b")
    active = live.providers.get_active()
    with live.sessions() as session:
        for agent_id, prompt in (
            ("control-supervisor", "你负责调度仿真团队。规划时只返回一个 simulation 类型任务，分配给 control-simulator；任务必须使用 write_file 保存 /artifacts/simulation.json。汇总时简洁总结，不新增任务。"),
            ("control-simulator", "你是仿真测试成员。对固定测试输入进行计算：目标开度20，允许偏差2，仿真预测开度20。必须调用 write_file 工具将 JSON {\"expected_state\":{\"opening\":20},\"status\":\"succeeded\"} 写到 /artifacts/simulation.json，写成功后再简短回答。不要调用其他工具，不要执行物理控制。"),
        ):
            live.agents.create(AgentCreateRequest(
                id=agent_id, name=agent_id, provider_id=active.provider_id, model=active.model,
                system_prompt=prompt, approval_policy="never", tool_ids=[], skill_names=[],
            ), context=requester, session=session)
        teams = TeamService(session, agent_service=live.agents, provider_service=live.providers)
        team = teams.create(requester, TeamCreateRequest(name="控制仿真发布门禁"))
        teams.save_draft(requester, team.id, TeamDraftUpdate(revision=1, draft=TeamDraft(
            supervisor=TeamMemberDraft(agent_id="control-supervisor", responsibility="规划一个仿真任务并汇总"),
            members=[TeamMemberDraft(agent_id="control-simulator", responsibility="计算目标开度20并将仿真成果保存为 simulation.json")],
            max_steps=3, max_parallel_members=1, timeout_seconds=300,
        )))
        published_version = teams.publish(requester, team.id)
        teams.set_enabled(requester, team.id, True)
        selected = teams.resolve_for_run(requester, team.id)
        assert selected.version_id == published_version.id
        conversation = Conversation(unit_id=requester.unit_id, project_id=requester.project_id, owner_id=requester.user_id, title="真实仿真控制验收")
        session.add(conversation)
        session.flush()
        message = Message(conversation_id=conversation.id, sequence=1, role="user", content="仿真目标开度20；仅创建仿真成果，必须写入 /artifacts/simulation.json。")
        session.add(message)
        session.flush()
        run = AgentRun(conversation_id=conversation.id, trigger_message_id=message.id, actor_type="team", actor_id=team.id,
            actor_version_id=selected.version_id, actor_roles_json=["project_admin"], status="running")
        session.add(run)
        session.commit()
        snapshot = ExecutionSnapshotService(session, live.agents, ConversationRepository(session), max_iterations=12, max_subagents=1).create(run.id)
        assert snapshot.payload.schema_version == "5"
        deadline = datetime.now(UTC) + timedelta(minutes=5)
        token = RunTokenService(session, signing_key=live.signing_key).issue(snapshot, {
            "snapshot.read", "model.invoke", "tool.invoke", "checkpoint.read", "checkpoint.write", "event.append",
            "artifact.create", "artifact.attest", "result.complete",
        }, deadline)
        request = RunExecutionRequest(run_id=run.id, agent_version=published_version.id, checkpoint_key="initial",
            deadline_at=deadline, execution_deadline_at=deadline, snapshot_id=snapshot.snapshot_id,
            snapshot_digest=snapshot.digest, gateway_url=live.gateway_url, run_token=token.value)
        run_id = run.id

    result = SandboxRuntime(RunnerGatewayClient.from_execution_request(request)).execute(request)
    assert result.status == "completed", f"real Team runtime failed: {result.error_code}; gateway={live.gateway_errors}"

    class PhysicalEndpoint:
        def __init__(self):
            self.logical_execute_count = 0
            self.logical_rollback_count = 0
            self.requests = []

        def __call__(self, request):
            self.requests.append(request)
            if request.url.path.endswith("/execute"):
                self.logical_execute_count += 1
            else:
                self.logical_rollback_count += 1
            return httpx.Response(200, json={"outcome": "succeeded", "external_id": "device-test-1", "summary": {"state": "accepted"}})

    executor = PhysicalEndpoint()
    registry = ControlExecutorRegistry.from_json(json.dumps({"gate": {
        "base_url": "https://physical.example/api", "token_env": "TEST_CONTROL_TOKEN", "timeout_seconds": 5,
    }}), lambda _name: "physical-endpoint-test-token", "production", http_transport=httpx.MockTransport(executor))
    try:
        with live.sessions() as session:
            artifact = session.scalars(select(ArtifactRecord).where(ArtifactRecord.run_id == run_id)).one()
            assert artifact.provenance["simulation_attestation"]["status"] == "succeeded"
            assert artifact.provenance["team_version_id"] == published_version.id
            assert artifact.provenance["member_agent_id"] == "control-simulator"
            assert artifact.provenance["simulation_attestation"]["artifact_sha256"] == artifact.sha256
            assert hashlib.sha256(live.storage.get_bytes(artifact.object_key)).hexdigest() == artifact.sha256
            assert json.loads(live.storage.get_bytes(artifact.object_key))["expected_state"] == {"opening": 20}
            assert session.get(AgentRun, run_id).status == "completed"
            events = set(session.scalars(select(RunEvent.event_type).where(RunEvent.run_id == run_id)))
            assert {"team.task.completed", "team.synthesis.completed"} <= events
            service = ControlService(session, executors=registry.executors)
            policy = service.create_policy(requester, ControlPolicyCreateRequest(
                asset_type="gate", command_type="set", parameter_schema={"type": "object", "properties": {"opening": {"type": "number"}}, "required": ["opening"]},
                max_command_lifetime_seconds=300, approval_mode="dual", deviation_thresholds={"opening": 2},
            ))
            now = datetime.now(UTC)
            command = service.propose(requester, ControlCommandProposeRequest(
                policy_id=policy.id, target={"asset_type": "gate", "id": "gate-1"}, parameters={"opening": 20},
                simulation_artifact_id=artifact.id, run_id=run_id, team_version_id=published_version.id,
                valid_from=now - timedelta(seconds=1), valid_until=now + timedelta(minutes=4),
                rollback_plan={"opening": 0}, expected_state={"opening": 20}, idempotency_key="proposal-1",
            ))
            assert command.team_version_id == published_version.id
            with pytest.raises(ControlForbiddenError):
                service.decide(command.id, requester, "approve")
            with pytest.raises(ControlNotFoundError) as hidden:
                service.get_command_detail(context(project="foreign", user="reviewer-a"), command.id)
            assert hidden.value.status_code == 404
            command = service.decide(command.id, reviewer_a, "approve")
            assert command.status == "pending_approval"
            command = service.decide(command.id, reviewer_b, "approve")
            assert command.status == "approved"
            executed = service.execute(command.id, requester, "same-key")
            assert executed.status == "awaiting_feedback"
            assert service.execute(command.id, requester, "same-key").status == "awaiting_feedback"
            assert executor.logical_execute_count == 1
            with pytest.raises(ControlValidationError):
                service.record_feedback(command.id, requester, ControlFeedbackRequest(actual_state={"opening": 50}, observed_at=now - timedelta(hours=1)))
            assert session.scalars(select(ControlFeedbackRecord).where(ControlFeedbackRecord.command_id == command.id)).all() == []
            feedback = service.record_feedback(command.id, requester, ControlFeedbackRequest(actual_state={"opening": 50}, observed_at=datetime.now(UTC)))
            assert feedback.status == "deviation"
            rollback = service.rollback(command.id, requester, "same-key")
            assert rollback.status == "rolled_back"
            assert service.rollback(command.id, requester, "same-key").status == "rolled_back"
            assert executor.logical_rollback_count == 1
            attempts = session.scalars(select(ControlExecutionAttempt).where(ControlExecutionAttempt.command_id == command.id).order_by(ControlExecutionAttempt.created_at)).all()
            assert [attempt.kind for attempt in attempts] == ["execute", "rollback"]
            assert all(attempt.outcome == "succeeded" and attempt.completed_at for attempt in attempts)
            assert attempts[0].external_idempotency_key != attempts[1].external_idempotency_key
            for physical_request, attempt in zip(executor.requests, attempts, strict=True):
                payload = json.loads(physical_request.content)
                assert payload["command_id"] == command.id
                assert payload["simulation_digest"] == artifact.sha256
                assert payload["policy_digest"] == command.policy_digest
                assert physical_request.headers["idempotency-key"] == attempt.external_idempotency_key
            assert json.loads(executor.requests[1].content)["rollback_plan"] == {"opening": 0}
            actions = list(session.scalars(select(AuditEvent.action).where(AuditEvent.resource_id == command.id)))
            assert {"control_command.proposed", "control_command.approved", "control_command.execute_started", "control_command.execute_completed", "control_command.feedback", "control_command.rollback_started", "control_command.rollback_completed"} <= set(actions)
            assert actions.count("control_command.approved") == 2
            assert actions.count("control_command.execute_started") == actions.count("control_command.rollback_started") == 1
            assert session.scalars(select(AuditEvent).where(AuditEvent.run_id == run_id, AuditEvent.action == "llm.invoke.succeeded")).first() is not None
    finally:
        for adapter in registry.executors.values():
            adapter._client.close()
