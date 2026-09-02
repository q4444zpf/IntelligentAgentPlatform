import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

from app.runtime import run_worker
from app.runtime.execution_contract import RunExecutionRequest
from app.runtime.execution_snapshot import (
    ExecutionSnapshotPayload,
    PublishedAgentSnapshot,
    PublishedTeamSnapshot,
    SnapshotMessage,
    SnapshotModelSelection,
    SnapshotRuntimeLimits,
    SnapshotSkill,
    SnapshotTeamMember,
    SnapshotTool,
    canonical_snapshot_bytes,
)
from app.runtime.gateway_model import RunnerGatewayModelError
from app.runtime.gateway_tools import RunnerApprovalInterruption
from app.runtime.runner_gateway_schemas import SnapshotResponse
from app.runtime.sandbox_runtime import SandboxRuntime


def _snapshot():
    payload = ExecutionSnapshotPayload(
        schema_version="2",
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
            system_prompt="system",
            context_prompt="context",
            approval_policy="never",
        ),
        model=SnapshotModelSelection(provider_id="provider-1", model="model-1"),
        messages=(
            SnapshotMessage(
                id="message-1",
                sequence=1,
                role="user",
                content="execute",
                created_at=datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
            ),
        ),
        tools=(
            SnapshotTool(
                tool_id="water.query",
                version="1",
                name="Water query",
                description="Query water data",
                input_schema={"type": "object", "properties": {}},
                published=True,
                enabled=True,
                source_available=True,
            ),
        ),
        limits=SnapshotRuntimeLimits(snapshot_max_bytes=1048576),
        created_at=datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
    )
    return SnapshotResponse(
        snapshot_id=payload.snapshot_id,
        run_id=payload.run_id,
        digest=hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest(),
        payload=payload,
    )


def _request(snapshot):
    return RunExecutionRequest(
        run_id="run-1",
        agent_version="agent-v1",
        checkpoint_key="checkpoint-1",
        deadline_at=datetime.now(UTC) + timedelta(minutes=5),
        snapshot_id="snapshot-1",
        snapshot_digest=snapshot.digest,
        gateway_url="http://api:8000/internal/runner",
        run_token="secret-token",
    )


def _schema_v5_team_snapshot(*, limits=None):
    base = _snapshot()
    tool = base.payload.tools[0]

    def member(agent_id, role, responsibility):
        return SnapshotTeamMember(
            agent_id=agent_id,
            role=role,
            responsibility=responsibility,
            agent_definition_digest=hashlib.sha256(agent_id.encode()).hexdigest(),
            agent=PublishedAgentSnapshot(
                id=agent_id,
                name=agent_id,
                description="",
                runtime_form="common",
                language="zh-CN",
                system_prompt=f"{agent_id} prompt",
                context_prompt="",
                approval_policy="never",
            ),
            model=SnapshotModelSelection(
                provider_id=f"{agent_id}-provider",
                model=f"{agent_id}-model",
            ),
            tool_ids=(tool.tool_id,),
            tools=(tool,),
        )

    supervisor = member("supervisor", "supervisor", "coordinate")
    members = (
        member("member-1", "member", "inspect"),
        member("member-2", "member", "review"),
    )
    actor = PublishedTeamSnapshot(
        id="team-1",
        version_id="version-1",
        version=1,
        definition_digest="d" * 64,
        supervisor=supervisor,
        members=members,
        max_steps=3,
        max_parallel_members=2,
        timeout_seconds=60,
        failure_strategy="fail_fast",
        tool_ids=(tool.tool_id,),
        name="Team",
        description="",
        runtime_form="common",
        language="zh-CN",
        system_prompt="supervisor prompt",
        context_prompt="",
        approval_policy="never",
    )
    payload = base.payload.model_copy(
        update={
            "schema_version": "5",
            "actor": actor,
            "model": supervisor.model,
            "tools": (tool,),
            "limits": limits or base.payload.limits,
        }
    )
    return base.model_copy(
        update={
            "payload": payload,
            "digest": hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest(),
        }
    )


class FakeGateway:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.snapshot_reads = 0
        self.checkpoint_reads = 0
        self.saved_checkpoints = []
        self.events = []
        self.completions = []
        self.model_calls = []
        self.tool_calls = []

    def get_snapshot(self):
        self.snapshot_reads += 1
        return self.snapshot

    def get_latest_checkpoint(self):
        self.checkpoint_reads += 1
        return {
            "checkpoint_key": "restored",
            "snapshot_digest": self.snapshot.digest,
            "state": {
                "messages": [{"role": "assistant", "content": "restored"}],
                "status": "running",
            },
        }

    def save_checkpoint(self, checkpoint_key, state, idempotency_key):
        self.saved_checkpoints.append((checkpoint_key, state, idempotency_key))
        return {
            "checkpoint_key": checkpoint_key,
            "snapshot_digest": self.snapshot.digest,
            "state": state,
        }

    def append_event(self, **request):
        self.events.append(request)
        return request

    def complete(self, request, idempotency_key):
        self.completions.append((request, idempotency_key))
        return request

    def invoke_model(self, request, idempotency_key):
        self.model_calls.append((request, idempotency_key))

    def invoke_tool(self, **request):
        self.tool_calls.append(request)

    def list_artifacts(self):
        return []


class FakeFactory:
    def __init__(self, graph):
        self.graph = graph
        self.calls = []

    def build(self, snapshot, **kwargs):
        self.calls.append((snapshot, kwargs))
        return self.graph


class CompletingGraph:
    def invoke(self, state, *, config=None):
        assert state["messages"][-1]["content"] == "restored"
        return {
            **state,
            "messages": [
                *state["messages"],
                {"role": "assistant", "content": "completed"},
            ],
            "status": "completed",
        }


class TeamCompletingGraph:
    def invoke(self, state, *, config=None):
        return {
            **state,
            "messages": [
                *state["messages"],
                {"role": "assistant", "content": "completed"},
            ],
            "status": "completed",
        }


class ModelInvokingFactory:
    def __init__(self):
        self.calls = []

    def build(self, snapshot, **kwargs):
        self.calls.append((snapshot, kwargs))
        model = kwargs["model"]

        class ModelInvokingGraph:
            def invoke(self, state, *, config=None):
                response = model.invoke([HumanMessage(content="execute")])
                return {
                    **state,
                    "messages": [
                        *state["messages"],
                        {"role": "assistant", "content": response.content or "done"},
                    ],
                    "status": "completed",
                }

        return ModelInvokingGraph()


class ModelInvokingGateway(FakeGateway):
    def __init__(self, snapshot, *, tool_name=None, reject_duplicate_keys=False):
        super().__init__(snapshot)
        self.tool_name = tool_name
        self.reject_duplicate_keys = reject_duplicate_keys
        self.idempotency_keys = set()

    def invoke_model(self, request, idempotency_key):
        if self.reject_duplicate_keys and idempotency_key in self.idempotency_keys:
            raise RunnerGatewayModelError("idempotency_conflict")
        self.idempotency_keys.add(idempotency_key)
        self.model_calls.append((request, idempotency_key))
        tool_calls = []
        if self.tool_name is not None:
            tool_calls.append(
                {
                    "id": f"call-{len(self.model_calls)}",
                    "name": self.tool_name,
                    "arguments": {},
                }
            )
        return {
            "content": "done",
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
            "tool_calls": tool_calls,
        }


def test_runtime_builds_agent_restores_checkpoint_streams_events_and_completes():
    snapshot = _snapshot()
    gateway = FakeGateway(snapshot)
    factory = FakeFactory(CompletingGraph())
    runtime = SandboxRuntime(gateway, agent_factory=factory)

    result = runtime.execute(_request(snapshot))

    assert result.status == "completed"
    assert gateway.snapshot_reads == 1
    assert gateway.checkpoint_reads == 1
    assert [event["event_type"] for event in gateway.events] == [
        "runner.started",
        "runner.completed",
    ]
    assert gateway.saved_checkpoints[0][0] == "langgraph"
    assert gateway.completions[0][0]["status"] == "completed"
    assert gateway.completions[0][0]["final_assistant_content"] == "completed"
    assert factory.calls[0][1]["backend"].list("/artifacts") == []


def test_digest_mismatch_stops_before_checkpoint_model_or_tool_call():
    snapshot = _snapshot()
    gateway = FakeGateway(snapshot.model_copy(update={"digest": "b" * 64}))
    runtime = SandboxRuntime(gateway, agent_factory=FakeFactory(CompletingGraph()))

    result = runtime.execute(_request(snapshot))

    assert result.status == "failed"
    assert result.error_code == "snapshot_invalid"
    assert gateway.checkpoint_reads == 0
    assert gateway.model_calls == []
    assert gateway.tool_calls == []


def test_approval_interruption_saves_checkpoint_and_returns_accepted_result():
    class ApprovalGraph:
        def invoke(self, state, *, config=None):
            raise RunnerApprovalInterruption("approval-1")

    snapshot = _snapshot()
    gateway = FakeGateway(snapshot)
    runtime = SandboxRuntime(gateway, agent_factory=FakeFactory(ApprovalGraph()))

    result = runtime.execute(_request(snapshot))

    assert result.status == "interrupted"
    assert result.error_code == "approval_required"
    assert gateway.saved_checkpoints[-1][0] == "approval-approval-1"
    assert gateway.events[-1]["event_type"] == "approval.required"
    assert gateway.completions[-1][0]["approval_id"] == "approval-1"


def test_raw_runtime_error_is_sanitized():
    class FailingGraph:
        def invoke(self, state, *, config=None):
            raise RuntimeError("password=secret C:/internal/path")

    snapshot = _snapshot()
    gateway = FakeGateway(snapshot)
    runtime = SandboxRuntime(gateway, agent_factory=FakeFactory(FailingGraph()))

    result = runtime.execute(_request(snapshot))

    assert result.status == "failed"
    assert result.error_code == "sandbox_failed"
    assert "secret" not in str(result)
    assert gateway.completions[-1][0] == {
        "status": "failed",
        "error_code": "sandbox_failed",
    }


def test_runtime_preserves_safe_model_limit_error_code():
    class LimitedGraph:
        def invoke(self, state, *, config=None):
            raise RunnerGatewayModelError("runtime_output_limit")

    snapshot = _snapshot()
    gateway = FakeGateway(snapshot)
    runtime = SandboxRuntime(gateway, agent_factory=FakeFactory(LimitedGraph()))

    result = runtime.execute(_request(snapshot))

    assert result.status == "failed"
    assert result.error_code == "runtime_output_limit"
    assert gateway.completions[-1][0] == {
        "status": "failed",
        "error_code": "runtime_output_limit",
    }


def test_runtime_passes_snapshot_limits_to_gateway_model():
    snapshot = _snapshot()
    gateway = FakeGateway(snapshot)
    factory = FakeFactory(CompletingGraph())
    runtime = SandboxRuntime(gateway, agent_factory=factory)

    runtime.execute(_request(snapshot))

    model = factory.calls[0][1]["model"]
    assert model.max_iterations == snapshot.payload.limits.max_iterations
    assert model.max_tool_calls == snapshot.payload.limits.max_tool_calls
    assert model.max_subagents == snapshot.payload.limits.max_subagents
    assert model.max_output_bytes == snapshot.payload.limits.max_output_bytes


def test_team_runtime_builds_each_member_from_its_own_immutable_boundary():
    base = _snapshot()
    supervisor = SnapshotTeamMember(
        agent_id="supervisor",
        role="supervisor",
        responsibility="coordinate",
        agent_definition_digest="a" * 64,
        agent=PublishedAgentSnapshot(
            id="supervisor",
            name="Supervisor",
            description="",
            runtime_form="common",
            language="zh-CN",
            system_prompt="supervisor prompt",
            context_prompt="supervisor context",
            approval_policy="never",
        ),
        model=SnapshotModelSelection(
            provider_id="supervisor-provider", model="supervisor-model"
        ),
        skill_names=("coordinate",),
        tool_ids=("water.supervise",),
        skills=(SnapshotSkill(name="coordinate"),),
        tools=(
            base.payload.tools[0].model_copy(
                update={"tool_id": "water.supervise"}
            ),
        ),
    )
    member = SnapshotTeamMember(
        agent_id="reviewer",
        role="member",
        responsibility="review",
        agent_definition_digest="b" * 64,
        agent=PublishedAgentSnapshot(
            id="reviewer",
            name="Reviewer",
            description="",
            runtime_form="common",
            language="zh-CN",
            system_prompt="member prompt",
            context_prompt="member context",
            approval_policy="never",
        ),
        model=SnapshotModelSelection(
            provider_id="member-provider", model="member-model"
        ),
        skill_names=("review",),
        tool_ids=("water.review",),
        skills=(SnapshotSkill(name="review"),),
        tools=(
            base.payload.tools[0].model_copy(update={"tool_id": "water.review"}),
        ),
    )
    payload = base.payload.model_copy(
        update={
            "schema_version": "5",
            "actor": PublishedTeamSnapshot(
                id="team-1",
                version_id="version-1",
                version=1,
                definition_digest="c" * 64,
                supervisor=supervisor,
                members=(member,),
                max_steps=2,
                max_parallel_members=1,
                timeout_seconds=60,
                failure_strategy="fail_fast",
                name="Team",
                description="",
                runtime_form="common",
                language="zh-CN",
                system_prompt="supervisor prompt",
                context_prompt="supervisor context",
                approval_policy="never",
            ),
            "model": supervisor.model,
            "skills": supervisor.skills,
            "tools": (*supervisor.tools, *member.tools),
        }
    )
    snapshot = base.model_copy(
        update={
            "payload": payload,
            "digest": hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest(),
        }
    )
    gateway = FakeGateway(snapshot)
    factory = FakeFactory(TeamCompletingGraph())

    result = SandboxRuntime(gateway, agent_factory=factory).execute(_request(snapshot))

    assert result.status == "completed"
    member_snapshot, member_kwargs = factory.calls[0]
    supervisor_snapshot, supervisor_kwargs = factory.calls[1]
    assert member_snapshot.system_prompt == "member prompt"
    assert member_snapshot.context_prompt == "member context"
    assert [skill.name for skill in member_snapshot.skills] == ["review"]
    assert member_kwargs["model"].provider_id == "member-provider"
    assert member_kwargs["model"].model_id == "member-model"
    assert member_kwargs["model"].member_agent_id == "reviewer"
    assert [tool.name for tool in member_kwargs["tools"]] == ["water.review"]
    assert supervisor_snapshot.system_prompt == "supervisor prompt"
    assert [skill.name for skill in supervisor_snapshot.skills] == ["coordinate"]
    assert supervisor_kwargs["model"].provider_id == "supervisor-provider"
    assert [tool.name for tool in supervisor_kwargs["tools"]] == [
        "water.supervise"
    ]


def test_team_runtime_allocates_unique_model_requests_across_members_and_supervisor():
    snapshot = _schema_v5_team_snapshot()
    gateway = ModelInvokingGateway(snapshot, reject_duplicate_keys=True)

    result = SandboxRuntime(
        gateway,
        agent_factory=ModelInvokingFactory(),
    ).execute(_request(snapshot))

    assert result.status == "completed"
    assert [
        (request["member_agent_id"], request["invocation_sequence"], key)
        for request, key in gateway.model_calls
    ] == [
        ("member-1", 0, "model-member-1-0"),
        ("member-2", 1, "model-member-2-1"),
        ("supervisor", 2, "model-supervisor-2"),
    ]


@pytest.mark.parametrize(
    ("limit_updates", "tool_name", "expected_error"),
    [
        ({"max_iterations": 2}, None, "runtime_iteration_limit"),
        ({"max_tool_calls": 2}, "water.query", "runtime_tool_call_limit"),
        ({"max_subagents": 2}, "task", "runtime_subagent_limit"),
    ],
)
def test_team_runtime_enforces_one_model_budget_across_all_members(
    limit_updates,
    tool_name,
    expected_error,
):
    base = _snapshot()
    limits = base.payload.limits.model_copy(update=limit_updates)
    snapshot = _schema_v5_team_snapshot(limits=limits)
    gateway = ModelInvokingGateway(snapshot, tool_name=tool_name)

    result = SandboxRuntime(
        gateway,
        agent_factory=ModelInvokingFactory(),
    ).execute(_request(snapshot))

    assert result.status == "failed"
    assert result.error_code == expected_error


def test_schema_v4_team_runtime_preserves_legacy_team_wide_construction():
    base = _snapshot()
    supervisor = SnapshotTeamMember(
        agent_id="supervisor",
        role="supervisor",
        responsibility="coordinate",
    )
    member = SnapshotTeamMember(
        agent_id="reviewer",
        role="member",
        responsibility="review",
    )
    payload = base.payload.model_copy(
        update={
            "schema_version": "4",
            "actor": PublishedTeamSnapshot(
                id="team-legacy",
                version_id="version-legacy",
                version=1,
                definition_digest="d" * 64,
                supervisor=supervisor,
                members=(member,),
                max_steps=2,
                max_parallel_members=1,
                timeout_seconds=60,
                failure_strategy="fail_fast",
                name="Legacy Team",
                description="",
                runtime_form="common",
                language="zh-CN",
                system_prompt="legacy supervisor",
                context_prompt="",
                approval_policy="never",
            ),
        }
    )
    snapshot = base.model_copy(
        update={
            "payload": payload,
            "digest": hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest(),
        }
    )
    gateway = FakeGateway(snapshot)
    factory = FakeFactory(TeamCompletingGraph())

    result = SandboxRuntime(gateway, agent_factory=factory).execute(
        _request(snapshot)
    )

    assert result.status == "completed"
    member_snapshot, member_kwargs = factory.calls[0]
    supervisor_snapshot, supervisor_kwargs = factory.calls[1]
    assert member_snapshot.system_prompt == (
        "You are the member member. Responsibility: review"
    )
    assert supervisor_snapshot.system_prompt == (
        "You are the supervisor member. Responsibility: coordinate"
    )
    assert member_kwargs["model"] is supervisor_kwargs["model"]
    assert [tool.name for tool in member_kwargs["tools"]] == ["water.query"]
    assert member_kwargs["tools"] is supervisor_kwargs["tools"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("completed", 0),
        ("interrupted", 0),
        ("failed", 1),
        ("cancelled", 3),
    ],
)
def test_worker_returns_fixed_exit_codes(monkeypatch, status, expected):
    request = _request(_snapshot())

    class ClientFactory:
        @staticmethod
        def from_execution_request(value):
            assert value is request
            return object()

    class Runtime:
        def __init__(self, _gateway):
            pass

        def execute(self, value):
            assert value is request
            return SimpleNamespace(status=status)

    monkeypatch.setattr(run_worker.sys, "argv", ["run_worker"])
    monkeypatch.setattr(run_worker, "load_execution_request", lambda: request)
    monkeypatch.setattr(run_worker, "RunnerGatewayClient", ClientFactory)
    monkeypatch.setattr(run_worker, "SandboxRuntime", Runtime)

    assert run_worker.main() == expected


def test_worker_rejects_invalid_execution_envelope(monkeypatch):
    monkeypatch.setattr(run_worker.sys, "argv", ["run_worker"])
    monkeypatch.setattr(
        run_worker,
        "load_execution_request",
        lambda: (_ for _ in ()).throw(KeyError("missing")),
    )

    assert run_worker.main() == 2
