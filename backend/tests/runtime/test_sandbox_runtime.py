import hashlib
import json
import subprocess
import sys
import threading
import time
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

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
from app.runtime.gateway_tools import (
    RunnerApprovalInterruption,
    RunnerGatewayToolError,
)
from app.runtime.runner_gateway_client import (
    RunnerGatewayBusinessError,
    RunnerGatewayClient,
    RunnerGatewayUnavailable,
)
from app.runtime.runner_gateway_schemas import SnapshotResponse
from app.runtime.sandbox_runtime import SandboxRuntime, _TeamTimedOut
from app.runtime.langgraph_runtime import LangGraphRuntimeAdapter, RuntimeState
from app.runtime.team_graph import (
    TeamBudgetState,
    TeamPlan,
    TeamSchedulerState,
    TeamTask,
)


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
    deadline = datetime.now(UTC) + timedelta(minutes=5)
    return RunExecutionRequest(
        run_id="run-1",
        agent_version="agent-v1",
        checkpoint_key="checkpoint-1",
        deadline_at=deadline,
        execution_deadline_at=deadline,
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


def _team_snapshot_with_schema(schema_version: str):
    snapshot = _schema_v5_team_snapshot()
    if schema_version == "5":
        return snapshot
    payload = snapshot.payload.model_copy(update={"schema_version": schema_version})
    return snapshot.model_copy(
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
        self.artifact_capability_registrations = []

    def get_snapshot(self):
        self.snapshot_reads += 1
        return self.snapshot

    def get_latest_checkpoint(self):
        self.checkpoint_reads += 1
        raise RunnerGatewayBusinessError("checkpoint_not_found")

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

    def register_artifact_capability(self, **request):
        self.artifact_capability_registrations.append(deepcopy(request))
        return f"capability:{request['invocation_id']}"

    def list_artifacts(self):
        return []


class DurableApprovalGateway(FakeGateway):
    def __init__(self, snapshot):
        super().__init__(snapshot)
        self.latest_checkpoint = None
        self.approval_granted = False

    def get_latest_checkpoint(self):
        self.checkpoint_reads += 1
        if self.latest_checkpoint is None:
            raise RunnerGatewayBusinessError("checkpoint_not_found")
        return deepcopy(self.latest_checkpoint)

    def save_checkpoint(self, checkpoint_key, state, idempotency_key):
        saved = {
            "checkpoint_key": checkpoint_key,
            "snapshot_digest": self.snapshot.digest,
            "state": deepcopy(state),
        }
        self.saved_checkpoints.append(
            (checkpoint_key, deepcopy(state), idempotency_key)
        )
        self.latest_checkpoint = saved
        return deepcopy(saved)

    def invoke_model(self, request, idempotency_key):
        self.model_calls.append((deepcopy(request), idempotency_key))
        return {
            "content": "prepared",
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
            "tool_calls": [],
        }

    def invoke_tool(self, **request):
        self.tool_calls.append(deepcopy(request))
        if not self.approval_granted:
            raise RunnerGatewayToolError(
                "tool_approval_required",
                approval_id="approval-1",
            )
        return {"approved": True}


class FakeFactory:
    def __init__(self, graph):
        self.graph = graph
        self.calls = []

    def build(self, snapshot, **kwargs):
        self.calls.append((snapshot, kwargs))
        return self.graph


class CompletingGraph:
    def invoke(self, state, *, config=None):
        assert state["messages"][-1]["content"] == "execute"
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


class TeamPlanGraph:
    def __init__(self, plan):
        self.plan = plan

    def invoke(self, state, *, config=None):
        return {
            **state,
            "messages": [
                *state["messages"],
                {"role": "assistant", "content": json.dumps(self.plan)},
            ],
            "status": "completed",
        }


class TextGraph:
    def __init__(self, content, *, delay=0, error=None, calls=None):
        self.content = content
        self.delay = delay
        self.error = error
        self.calls = calls

    def invoke(self, state, *, config=None):
        if self.calls is not None:
            self.calls.append(deepcopy(state))
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return {
            **state,
            "messages": [
                *state["messages"],
                {"role": "assistant", "content": self.content},
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
        planning = (
            request.get("member_agent_id") == "supervisor"
            and len(self.model_calls) == 1
        )
        if self.tool_name is not None and not planning:
            tool_calls.append(
                {
                    "id": f"call-{len(self.model_calls)}",
                    "name": self.tool_name,
                    "arguments": {},
                }
            )
        return {
            "content": (
                json.dumps({
                    "tasks": [
                        {
                            "id": "member-1-task",
                            "member_id": "member-1",
                            "objective": "inspect",
                            "depends_on": [],
                            "position": 0,
                        },
                        {
                            "id": "member-2-task",
                            "member_id": "member-2",
                            "objective": "review",
                            "depends_on": [],
                            "position": 1,
                        },
                    ],
                })
                if planning
                else "done"
            ),
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
            "tool_calls": tool_calls,
        }


def test_runtime_builds_agent_on_fresh_run_streams_events_and_completes():
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
    assert isinstance(factory.calls[0][1]["checkpointer"], InMemorySaver)


def test_expired_execution_deadline_reports_timeout_before_snapshot_transport():
    snapshot = _snapshot()
    gateway = FakeGateway(snapshot)
    request = _request(snapshot).model_copy(
        update={
            "execution_deadline_at": datetime.now(UTC) - timedelta(seconds=1)
        }
    )

    result = SandboxRuntime(gateway).execute(request)

    assert result.status == "failed"
    assert result.error_code == "sandbox_timeout"
    assert gateway.snapshot_reads == 0
    assert gateway.completions == [
        (
            {"status": "failed", "error_code": "sandbox_timeout"},
            "completion:failed",
        )
    ]


def test_gateway_failure_observed_after_execution_deadline_reports_timeout():
    snapshot = _snapshot()

    class ExpiringGateway(FakeGateway):
        def get_snapshot(self):
            self.snapshot_reads += 1
            raise RunnerGatewayUnavailable()

    gateway = ExpiringGateway(snapshot)
    request = _request(snapshot).model_copy(
        update={
            "execution_deadline_at": datetime.now(UTC)
            + timedelta(seconds=0.1)
        }
    )
    ticks = iter([0.0, 0.2])

    result = SandboxRuntime(
        gateway,
        monotonic=ticks.__next__,
    ).execute(request)

    assert result.status == "failed"
    assert result.error_code == "sandbox_timeout"
    assert gateway.completions == [
        (
            {"status": "failed", "error_code": "sandbox_timeout"},
            "completion:failed",
        )
    ]


def test_real_gateway_slow_drip_expiry_completes_as_sandbox_timeout():
    snapshot = _snapshot()
    snapshot_body = snapshot.model_dump_json().encode()
    completions = []
    snapshot_body_fully_sent = threading.Event()

    class SlowSnapshotHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(snapshot_body)))
            self.end_headers()
            chunk_size = max(1, len(snapshot_body) // 12)
            try:
                for offset in range(0, len(snapshot_body), chunk_size):
                    self.wfile.write(
                        snapshot_body[offset : offset + chunk_size]
                    )
                    self.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                return
            snapshot_body_fully_sent.set()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            completions.append(json.loads(self.rfile.read(length)))
            body = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    class TestServer(ThreadingHTTPServer):
        daemon_threads = True

    server = TestServer(("127.0.0.1", 0), SlowSnapshotHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    request = _request(snapshot).model_copy(
        update={
            "deadline_at": datetime.now(UTC) + timedelta(seconds=5),
            "execution_deadline_at": datetime.now(UTC)
            + timedelta(seconds=0.25),
            "gateway_url": f"http://127.0.0.1:{server.server_port}",
        }
    )
    gateway = RunnerGatewayClient.from_execution_request(request)
    try:
        result = SandboxRuntime(gateway).execute(request)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.status == "failed"
    assert result.error_code == "sandbox_timeout"
    assert completions == [
        {"status": "failed", "error_code": "sandbox_timeout"}
    ]
    assert not snapshot_body_fully_sent.is_set()


def test_model_gateway_error_observed_after_execution_deadline_reports_timeout():
    snapshot = _snapshot()

    class ExpiringModelGateway(FakeGateway):
        def invoke_model(self, request, idempotency_key):
            self.model_calls.append((request, idempotency_key))
            raise RunnerGatewayUnavailable()

    gateway = ExpiringModelGateway(snapshot)
    request = _request(snapshot).model_copy(
        update={
            "execution_deadline_at": datetime.now(UTC)
            + timedelta(seconds=0.1)
        }
    )
    ticks = iter([0.0, 0.2])

    result = SandboxRuntime(
        gateway,
        agent_factory=ModelInvokingFactory(),
        monotonic=ticks.__next__,
    ).execute(request)

    assert result.status == "failed"
    assert result.error_code == "sandbox_timeout"
    assert len(gateway.model_calls) == 1
    assert gateway.completions[-1] == (
        {"status": "failed", "error_code": "sandbox_timeout"},
        "completion:failed",
    )


def test_team_execution_receives_float_monotonic_deadline_keyword():
    snapshot = _schema_v5_team_snapshot()
    gateway = FakeGateway(snapshot)

    class DeadlineContractRuntime(SandboxRuntime):
        received_deadline = None

        def _execute_team(self, *, monotonic_deadline: float, **_kwargs):
            self.received_deadline = monotonic_deadline
            return SimpleNamespace(status="completed", content="done")

    runtime = DeadlineContractRuntime(gateway, monotonic=lambda: 10.0)

    result = runtime.execute(_request(snapshot))

    assert result.status == "completed"
    assert type(runtime.received_deadline) is float


def test_agent_checkpoint_gateway_not_found_fails_closed_without_starting_fresh():
    class MissingGatewayRoute(FakeGateway):
        def get_latest_checkpoint(self):
            self.checkpoint_reads += 1
            raise RunnerGatewayBusinessError("runner_gateway_not_found")

    snapshot = _snapshot()
    gateway = MissingGatewayRoute(snapshot)
    factory = FakeFactory(CompletingGraph())

    result = SandboxRuntime(gateway, agent_factory=factory).execute(
        _request(snapshot)
    )

    assert result.status == "failed"
    assert result.error_code == "runner_gateway_not_found"
    assert factory.calls == []
    assert gateway.model_calls == []
    assert gateway.tool_calls == []
    assert gateway.events == []
    assert gateway.completions[-1][0] == {
        "status": "failed",
        "error_code": "runner_gateway_not_found",
    }


@pytest.mark.parametrize(
    "checkpoint_state",
    [
        {},
        {
            "status": "waiting_approval",
            "approval_id": "legacy-approval",
        },
        {
            "__langgraph_checkpoint__": {
                "version": 1,
            },
        },
    ],
    ids=("minimal", "legacy-approval", "adapter-only"),
)
def test_agent_restart_fails_closed_for_checkpoint_without_runtime_metadata(
    checkpoint_state,
):
    snapshot = _snapshot()
    gateway = DurableApprovalGateway(snapshot)
    gateway.latest_checkpoint = {
        "checkpoint_key": "legacy-agent-checkpoint",
        "snapshot_digest": snapshot.digest,
        "state": deepcopy(checkpoint_state),
    }
    factory = FakeFactory(CompletingGraph())

    result = SandboxRuntime(gateway, agent_factory=factory).execute(
        _request(snapshot)
    )

    assert result.status == "failed"
    assert result.error_code == "agent_recovery_required"
    assert factory.calls == []
    assert gateway.model_calls == []
    assert gateway.tool_calls == []
    assert gateway.events == []


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


def test_agent_approval_restart_preserves_exact_graph_frontier_and_gateway_sequences():
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    class WorkflowState(TypedDict, total=False):
        messages: list[dict]
        prepared: bool

    prepared_calls = []

    class Factory:
        def build(self, _agent, **kwargs):
            model = kwargs["model"]
            tool = kwargs["tools"][0]
            graph = StateGraph(WorkflowState)

            def prepare(_state):
                prepared_calls.append("prepare")
                model.invoke([HumanMessage(content="prepare operation")])
                return {"prepared": True}

            def approval(state):
                tool.run({}, tool_call_id="approval-tool-call")
                return {
                    "messages": [
                        *state["messages"],
                        {"role": "assistant", "content": "approved"},
                    ]
                }

            graph.add_node("prepare", prepare)
            graph.add_node("approval", approval)
            graph.add_edge(START, "prepare")
            graph.add_edge("prepare", "approval")
            graph.add_edge("approval", END)
            return graph.compile(checkpointer=kwargs["checkpointer"])

    snapshot = _snapshot()
    gateway = DurableApprovalGateway(snapshot)

    interrupted = SandboxRuntime(gateway, agent_factory=Factory()).execute(
        _request(snapshot)
    )

    assert interrupted.status == "interrupted"
    assert gateway.latest_checkpoint["checkpoint_key"] == "approval-approval-1"

    gateway.approval_granted = True
    resumed = SandboxRuntime(gateway, agent_factory=Factory()).execute(
        _request(snapshot)
    )

    assert resumed.status == "completed"
    assert prepared_calls == ["prepare"]
    assert [key for _request_body, key in gateway.model_calls] == ["model-0"]
    assert len(gateway.tool_calls) == 2
    assert gateway.tool_calls[0] == gateway.tool_calls[1]
    assert gateway.tool_calls[0]["tool_call_id"] == "approval-tool-call"
    assert [event["event_type"] for event in gateway.events] == [
        "runner.started",
        "approval.required",
        "runner.completed",
    ]
    assert [event["sequence"] for event in gateway.events] == [1, 2, 3]


def test_agent_approval_checkpoint_save_failure_is_fail_closed_before_event():
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    class WorkflowState(TypedDict, total=False):
        messages: list[dict]
        prepared: bool

    class RejectingApprovalCheckpointGateway(DurableApprovalGateway):
        def save_checkpoint(self, checkpoint_key, state, idempotency_key):
            if "__sandbox_runtime__" in state:
                raise RunnerGatewayBusinessError("checkpoint_too_large")
            return super().save_checkpoint(
                checkpoint_key,
                state,
                idempotency_key,
            )

    prepared_calls = []
    factory_calls = []

    class Factory:
        def build(self, _agent, **kwargs):
            factory_calls.append("build")
            graph = StateGraph(WorkflowState)

            def prepare(_state):
                prepared_calls.append("prepare")
                return {"prepared": True}

            def approval(_state):
                raise RunnerApprovalInterruption("oversized-approval")

            graph.add_node("prepare", prepare)
            graph.add_node("approval", approval)
            graph.add_edge(START, "prepare")
            graph.add_edge("prepare", "approval")
            graph.add_edge("approval", END)
            return graph.compile(checkpointer=kwargs["checkpointer"])

    snapshot = _snapshot()
    gateway = RejectingApprovalCheckpointGateway(snapshot)

    failed_save = SandboxRuntime(gateway, agent_factory=Factory()).execute(
        _request(snapshot)
    )

    assert failed_save.status == "failed"
    assert failed_save.error_code == "checkpoint_too_large"
    assert gateway.latest_checkpoint["checkpoint_key"] == "interrupted"
    assert "__langgraph_checkpoint__" in gateway.latest_checkpoint["state"]
    assert "__sandbox_runtime__" not in gateway.latest_checkpoint["state"]
    assert [event["event_type"] for event in gateway.events] == ["runner.started"]

    restarted = SandboxRuntime(gateway, agent_factory=Factory()).execute(
        _request(snapshot)
    )

    assert restarted.status == "failed"
    assert restarted.error_code == "agent_recovery_required"
    assert factory_calls == ["build"]
    assert prepared_calls == ["prepare"]
    assert [event["event_type"] for event in gateway.events] == ["runner.started"]


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
    class BoundaryFactory(FakeFactory):
        def __init__(self):
            super().__init__(TeamCompletingGraph())
            self.planned = False

        def build(self, snapshot, **kwargs):
            self.calls.append((snapshot, kwargs))
            if snapshot.agent_id == "supervisor" and not self.planned:
                self.planned = True
                return TeamPlanGraph({
                    "tasks": [{
                        "id": "review",
                        "member_id": "reviewer",
                        "objective": "review",
                        "depends_on": [],
                        "position": 0,
                    }],
                })
            return self.graph

    factory = BoundaryFactory()

    result = SandboxRuntime(gateway, agent_factory=factory).execute(_request(snapshot))

    assert result.status == "completed"
    planning_snapshot, _planning_kwargs = factory.calls[0]
    member_snapshot, member_kwargs = factory.calls[1]
    supervisor_snapshot, supervisor_kwargs = factory.calls[2]
    assert planning_snapshot.agent_id == "supervisor"
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
    calls = [
        (request["member_agent_id"], request["invocation_sequence"], key)
        for request, key in gateway.model_calls
    ]
    assert calls[0] == ("supervisor", 0, "model-supervisor-0")
    assert set(calls[1:3]) == {
        ("member-1", 1, "model-member-1-1"),
        ("member-2", 2, "model-member-2-2"),
    }
    assert calls[3] == ("supervisor", 3, "model-supervisor-3")


def test_team_runtime_namespaces_shared_model_tool_call_id_by_task_invocation():
    snapshot = _schema_v5_team_snapshot()
    plan = {
        "tasks": [
            {
                "id": "member-1-task",
                "member_id": "member-1",
                "objective": "inspect",
                "depends_on": [],
                "position": 0,
            },
            {
                "id": "member-2-task",
                "member_id": "member-2",
                "objective": "review",
                "depends_on": [],
                "position": 1,
            },
        ]
    }

    class DuplicateRejectingGateway(FakeGateway):
        def __init__(self):
            super().__init__(snapshot)
            self.tool_call_ids = set()

        def invoke_tool(self, **request):
            if request["tool_call_id"] in self.tool_call_ids:
                raise RunnerGatewayToolError("tool_duplicate_call")
            self.tool_call_ids.add(request["tool_call_id"])
            self.tool_calls.append(deepcopy(request))
            return {"ok": True}

    class ToolCallingGraph:
        def __init__(self, tool):
            self.tool = tool

        def invoke(self, state, *, config=None):
            self.tool.run({}, tool_call_id="shared-model-call")
            return {
                **state,
                "messages": [
                    *state["messages"],
                    {"role": "assistant", "content": "done"},
                ],
                "status": "completed",
            }

    class Factory:
        def __init__(self):
            self.supervisor_calls = 0

        def build(self, member, **kwargs):
            if member.agent_id == "supervisor":
                self.supervisor_calls += 1
                if self.supervisor_calls == 1:
                    return TeamPlanGraph(plan)
                return TextGraph("synthesized")
            return ToolCallingGraph(kwargs["tools"][0])

    gateway = DuplicateRejectingGateway()
    result = SandboxRuntime(gateway, agent_factory=Factory()).execute(
        _request(snapshot)
    )

    assert result.status == "completed"
    assert {request["tool_call_id"] for request in gateway.tool_calls} == {
        "team:version-1:member-1-task:shared-model-call",
        "team:version-1:member-2-task:shared-model-call",
    }
    assert {request["idempotency_key"] for request in gateway.tool_calls} == {
        "tool:team:version-1:member-1-task:shared-model-call:0",
        "tool:team:version-1:member-2-task:shared-model-call:0",
    }


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


@pytest.mark.parametrize(("max_subagents", "expected_peak"), [(1, 1), (2, 2)])
def test_team_runtime_executes_typed_plan_in_bounded_parallel_batches_and_joins_by_position(
    max_subagents,
    expected_peak,
):
    base = _schema_v5_team_snapshot()
    limits = base.payload.limits.model_copy(
        update={"max_iterations": 8, "max_subagents": max_subagents}
    )
    actor = base.payload.actor.model_copy(update={"max_parallel_members": 2})
    payload = base.payload.model_copy(update={"actor": actor, "limits": limits})
    snapshot = base.model_copy(update={
        "payload": payload,
        "digest": hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest(),
    })
    plan = {
        "tasks": [
            {
                "id": "slow",
                "member_id": "member-1",
                "objective": "inspect",
                "depends_on": [],
                "position": 0,
            },
            {
                "id": "fast",
                "member_id": "member-2",
                "objective": "review",
                "depends_on": [],
                "position": 1,
            },
        ]
    }
    lock = threading.Lock()
    active = 0
    peak = 0
    synthesis_states = []

    class MeasuredGraph(TextGraph):
        def invoke(self, state, *, config=None):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                return super().invoke(state, config=config)
            finally:
                with lock:
                    active -= 1

    class Factory:
        def __init__(self):
            self.supervisor_calls = 0

        def build(self, member, **kwargs):
            if member.agent_id == "supervisor":
                self.supervisor_calls += 1
                if self.supervisor_calls == 1:
                    return TeamPlanGraph(plan)
                return TextGraph("synthesis", calls=synthesis_states)
            return MeasuredGraph(
                f"{member.agent_id}-result",
                delay=0.08 if member.agent_id == "member-1" else 0.02,
            )

    gateway = FakeGateway(snapshot)
    result = SandboxRuntime(gateway, agent_factory=Factory()).execute(
        _request(snapshot)
    )

    assert result.status == "completed"
    assert peak == expected_peak
    synthesis_input = synthesis_states[0]["messages"][-1]["content"]
    assert synthesis_input.index("slow: member-1-result") < synthesis_input.index(
        "fast: member-2-result"
    )


@pytest.mark.parametrize(
    ("failure_strategy", "expected_status", "expect_synthesis"),
    [
        ("fail_fast", "failed", False),
        ("continue_then_synthesize", "completed", True),
    ],
)
def test_team_runtime_applies_failure_strategy_and_labels_partial_content(
    failure_strategy,
    expected_status,
    expect_synthesis,
):
    base = _schema_v5_team_snapshot()
    actor = base.payload.actor.model_copy(update={
        "failure_strategy": failure_strategy,
        "max_parallel_members": 2,
    })
    payload = base.payload.model_copy(update={
        "actor": actor,
        "limits": base.payload.limits.model_copy(update={"max_iterations": 8}),
    })
    snapshot = base.model_copy(update={
        "payload": payload,
        "digest": hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest(),
    })
    plan = {
        "tasks": [
            {
                "id": "failed-task",
                "member_id": "member-1",
                "objective": "inspect",
                "depends_on": [],
                "position": 0,
            },
            {
                "id": "successful-task",
                "member_id": "member-2",
                "objective": "review",
                "depends_on": [],
                "position": 1,
            },
        ]
    }
    synthesis_states = []

    class Factory:
        def __init__(self):
            self.supervisor_calls = 0

        def build(self, member, **kwargs):
            if member.agent_id == "supervisor":
                self.supervisor_calls += 1
                if self.supervisor_calls == 1:
                    return TeamPlanGraph(plan)
                return TextGraph("bounded summary", calls=synthesis_states)
            if member.agent_id == "member-1":
                return TextGraph("", error=RuntimeError("secret failure detail"))
            return TextGraph("successful result")

    gateway = FakeGateway(snapshot)
    result = SandboxRuntime(gateway, agent_factory=Factory()).execute(
        _request(snapshot)
    )

    assert result.status == expected_status
    synthesis_events = [
        event for event in gateway.events
        if event["event_type"] == "team.synthesis.completed"
    ]
    assert bool(synthesis_events) is expect_synthesis
    if expect_synthesis:
        synthesis_input = synthesis_states[0]["messages"][-1]["content"]
        assert "failed-task" in synthesis_input
        assert "member_execution_failed" in synthesis_input
        final_content = gateway.completions[-1][0]["final_assistant_content"]
        assert final_content.startswith("Partial completion:")
        assert "secret failure detail" not in final_content
        assert synthesis_events[0]["payload"]["partial"] is True


def test_team_fail_fast_failure_wins_over_parallel_approval_interruption():
    base = _schema_v5_team_snapshot()
    actor = base.payload.actor.model_copy(
        update={"failure_strategy": "fail_fast", "max_parallel_members": 2}
    )
    payload = base.payload.model_copy(
        update={
            "actor": actor,
            "limits": base.payload.limits.model_copy(update={"max_iterations": 8}),
        }
    )
    snapshot = base.model_copy(
        update={
            "payload": payload,
            "digest": hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest(),
        }
    )
    plan = {
        "tasks": [
            {
                "id": "failed-task",
                "member_id": "member-1",
                "objective": "inspect",
                "depends_on": [],
                "position": 0,
            },
            {
                "id": "approval-task",
                "member_id": "member-2",
                "objective": "operate",
                "depends_on": [],
                "position": 1,
            },
        ]
    }

    class Factory:
        def __init__(self):
            self.supervisor_calls = 0

        def build(self, member, **_kwargs):
            if member.agent_id == "supervisor":
                self.supervisor_calls += 1
                return TeamPlanGraph(plan)
            if member.agent_id == "member-1":
                return TextGraph("", error=RuntimeError("member failed"))
            return TextGraph(
                "",
                error=RunnerApprovalInterruption("approval-member-2"),
            )

    gateway = DurableApprovalGateway(snapshot)
    result = SandboxRuntime(gateway, agent_factory=Factory()).execute(
        _request(snapshot)
    )

    assert result.status == "failed"
    assert result.error_code == "sandbox_failed"
    assert gateway.latest_checkpoint["state"]["failed_results"] == [
        {
            "task_id": "failed-task",
            "member_agent_id": "member-1",
            "position": 0,
            "error_code": "member_execution_failed",
        }
    ]
    assert not any(
        completion[0].get("status") == "interrupted"
        for completion in gateway.completions
    )


def test_team_fail_fast_does_not_wait_for_blocked_sibling_or_persist_its_result():
    snapshot = _schema_v5_team_snapshot()
    plan = {
        "tasks": [
            {
                "id": "blocked-task",
                "member_id": "member-1",
                "objective": "inspect",
                "depends_on": [],
                "position": 0,
            },
            {
                "id": "failed-task",
                "member_id": "member-2",
                "objective": "review",
                "depends_on": [],
                "position": 1,
            },
        ]
    }
    release_blocked_member = threading.Event()

    class Factory:
        def __init__(self):
            self.supervisor_calls = 0

        def build(self, member, **_kwargs):
            if member.agent_id == "supervisor":
                self.supervisor_calls += 1
                return SimpleNamespace(stage="planning")
            if member.agent_id == "member-1":
                return SimpleNamespace(stage="blocked")
            return SimpleNamespace(stage="failed")

    class CheckpointingAdapter:
        def __init__(self, graph, *, checkpoint_store=None):
            self.graph = graph
            self.checkpoint_store = checkpoint_store

        def invoke(self, _state, *, metadata=None):
            del metadata
            if self.graph.stage == "planning":
                return SimpleNamespace(content=json.dumps(plan))
            if self.graph.stage == "failed":
                raise RuntimeError("member failed")
            assert release_blocked_member.wait(2)
            assert self.checkpoint_store is not None
            self.checkpoint_store.save(
                "run-1", "late-member", {"stage": "late-member"}
            )
            return SimpleNamespace(content="late result")

    gateway = FakeGateway(snapshot)
    release_timer = threading.Timer(0.75, release_blocked_member.set)
    release_timer.start()
    started_at = time.monotonic()
    result = SandboxRuntime(
        gateway,
        agent_factory=Factory(),
        runtime_adapter_type=CheckpointingAdapter,
    ).execute(
        _request(snapshot)
    )

    assert result.status == "failed"
    assert time.monotonic() - started_at < 0.5
    time.sleep(0.8)
    assert not any(
        invocation.get("runtime_state") == {"stage": "late-member"}
        for _key, state, _idempotency_key in gateway.saved_checkpoints
        for invocation in state["active_invocations"]
    )


@pytest.mark.parametrize("schema_version", ["4", "5"])
def test_team_fail_fast_fences_blocked_sibling_before_external_tool_invocation(
    schema_version,
):
    snapshot = _team_snapshot_with_schema(schema_version)
    plan = {
        "tasks": [
            {
                "id": "blocked-task",
                "member_id": "member-1",
                "objective": "operate",
                "depends_on": [],
                "position": 0,
            },
            {
                "id": "failed-task",
                "member_id": "member-2",
                "objective": "fail",
                "depends_on": [],
                "position": 1,
            },
        ]
    }
    blocked_member_ready = threading.Event()
    release_tool_attempt = threading.Event()
    tool_attempt_finished = threading.Event()
    legacy_cancellation_hooked = threading.Event()

    class Factory:
        def build(self, member, **kwargs):
            if member.agent_id == "supervisor":
                return SimpleNamespace(stage="planning")
            if member.agent_id == "member-1":
                cancellation_event = kwargs["model"].cancellation_event
                if (
                    schema_version == "4"
                    and isinstance(cancellation_event, threading.Event)
                    and not legacy_cancellation_hooked.is_set()
                ):
                    legacy_cancellation_hooked.set()
                    original_set = cancellation_event.set

                    def set_after_released_attempt():
                        release_tool_attempt.set()
                        assert tool_attempt_finished.wait(2)
                        original_set()

                    cancellation_event.set = set_after_released_attempt
                return SimpleNamespace(stage="blocked", tool=kwargs["tools"][0])
            return SimpleNamespace(stage="failed")

    class ExternalToolAdapter:
        def __init__(self, graph, *, checkpoint_store=None):
            self.graph = graph
            self.checkpoint_store = checkpoint_store

        def invoke(self, _state, *, metadata=None):
            del metadata
            if self.graph.stage == "planning":
                return SimpleNamespace(content=json.dumps(plan))
            if self.graph.stage == "failed":
                assert blocked_member_ready.wait(2)
                raise RuntimeError("member failed")
            blocked_member_ready.set()
            assert release_tool_attempt.wait(2)
            try:
                self.graph.tool.run({}, tool_call_id="late-external-tool")
            except RunnerGatewayToolError:
                pass
            finally:
                tool_attempt_finished.set()
            return SimpleNamespace(content="late result")

    gateway = FakeGateway(snapshot)
    result = SandboxRuntime(
        gateway,
        agent_factory=Factory(),
        runtime_adapter_type=ExternalToolAdapter,
    ).execute(_request(snapshot))

    assert result.status == "failed"
    release_tool_attempt.set()
    assert tool_attempt_finished.wait(2)
    assert gateway.tool_calls == []


@pytest.mark.parametrize("schema_version", ["4", "5"])
def test_team_fail_fast_fences_blocked_sibling_before_external_model_invocation(
    schema_version,
):
    snapshot = _team_snapshot_with_schema(schema_version)
    plan = {
        "tasks": [
            {
                "id": "blocked-task",
                "member_id": "member-1",
                "objective": "analyze",
                "depends_on": [],
                "position": 0,
            },
            {
                "id": "failed-task",
                "member_id": "member-2",
                "objective": "fail",
                "depends_on": [],
                "position": 1,
            },
        ]
    }
    blocked_member_ready = threading.Event()
    release_model_attempt = threading.Event()
    model_attempt_finished = threading.Event()
    legacy_cancellation_hooked = threading.Event()

    class Factory:
        def build(self, member, **kwargs):
            if member.agent_id == "supervisor":
                return SimpleNamespace(stage="planning")
            if member.agent_id == "member-1":
                cancellation_event = kwargs["model"].cancellation_event
                if (
                    schema_version == "4"
                    and isinstance(cancellation_event, threading.Event)
                    and not legacy_cancellation_hooked.is_set()
                ):
                    legacy_cancellation_hooked.set()
                    original_set = cancellation_event.set

                    def set_after_released_attempt():
                        release_model_attempt.set()
                        assert model_attempt_finished.wait(2)
                        original_set()

                    cancellation_event.set = set_after_released_attempt
                return SimpleNamespace(stage="blocked", model=kwargs["model"])
            return SimpleNamespace(stage="failed")

    class ExternalModelAdapter:
        def __init__(self, graph, *, checkpoint_store=None):
            self.graph = graph
            self.checkpoint_store = checkpoint_store

        def invoke(self, _state, *, metadata=None):
            del metadata
            if self.graph.stage == "planning":
                return SimpleNamespace(content=json.dumps(plan))
            if self.graph.stage == "failed":
                assert blocked_member_ready.wait(2)
                raise RuntimeError("member failed")
            blocked_member_ready.set()
            assert release_model_attempt.wait(2)
            try:
                self.graph.model.invoke([HumanMessage(content="late model call")])
            except RunnerGatewayModelError:
                pass
            finally:
                model_attempt_finished.set()
            return SimpleNamespace(content="late result")

    gateway = FakeGateway(snapshot)
    result = SandboxRuntime(
        gateway,
        agent_factory=Factory(),
        runtime_adapter_type=ExternalModelAdapter,
    ).execute(_request(snapshot))

    assert result.status == "failed"
    release_model_attempt.set()
    assert model_attempt_finished.wait(2)
    assert gateway.model_calls == []


def test_team_fail_fast_resume_stops_before_replaying_after_persisted_failure():
    snapshot = _schema_v5_team_snapshot(
        limits=_snapshot().payload.limits.model_copy(update={"max_iterations": 8})
    )
    plan = TeamPlan(
        tasks=(
            TeamTask(
                id="failed-task",
                member_id="member-1",
                objective="inspect",
                position=0,
            ),
            TeamTask(
                id="pending-task",
                member_id="member-2",
                objective="operate",
                position=1,
            ),
        )
    )
    checkpoint = TeamSchedulerState(
        stage="executing",
        snapshot_digest=snapshot.digest,
        team_version_id="version-1",
        plan=plan,
        pending_task_ids=("pending-task",),
        started_task_ids=("failed-task",),
        failed_results=(
            {
                "task_id": "failed-task",
                "member_agent_id": "member-1",
                "position": 0,
                "error_code": "member_execution_failed",
            },
        ),
        budget=TeamBudgetState(
            next_invocation_sequence=0,
            tool_call_count=0,
            subagent_call_count=0,
        ),
    ).model_dump(mode="json")

    class CheckpointGateway(DurableApprovalGateway):
        def __init__(self, value):
            super().__init__(value)
            self.latest_checkpoint = {
                "checkpoint_key": "team-scheduler",
                "snapshot_digest": value.digest,
                "state": checkpoint,
            }

    class RejectingFactory:
        def __init__(self):
            self.build_calls = 0

        def build(self, *_args, **_kwargs):
            self.build_calls += 1
            raise AssertionError("fail-fast checkpoint replayed pending work")

    gateway = CheckpointGateway(snapshot)
    factory = RejectingFactory()
    result = SandboxRuntime(gateway, agent_factory=factory).execute(
        _request(snapshot)
    )

    assert result.status == "failed"
    assert result.error_code == "sandbox_failed"
    assert factory.build_calls == 0


@pytest.mark.parametrize(
    "checkpoint_state",
    [
        {"kind": "unrelated_checkpoint"},
        {"kind": "team_scheduler"},
        None,
    ],
)
def test_team_runtime_fails_recovery_when_a_found_checkpoint_is_not_valid_team_state(
    checkpoint_state,
):
    snapshot = _schema_v5_team_snapshot()

    class InvalidCheckpointGateway(FakeGateway):
        def get_latest_checkpoint(self):
            self.checkpoint_reads += 1
            return {
                "checkpoint_key": "legacy-or-corrupt",
                "snapshot_digest": snapshot.digest,
                "state": checkpoint_state,
            }

    class RejectingFactory:
        def __init__(self):
            self.build_calls = 0

        def build(self, *_args, **_kwargs):
            self.build_calls += 1
            raise AssertionError("invalid checkpoint entered fresh execution")

    gateway = InvalidCheckpointGateway(snapshot)
    factory = RejectingFactory()
    result = SandboxRuntime(gateway, agent_factory=factory).execute(
        _request(snapshot)
    )

    assert result.status == "failed"
    assert result.error_code == "team_recovery_required"
    assert factory.build_calls == 0


def test_team_timeout_applies_during_execution_before_member_dequeue():
    base = _schema_v5_team_snapshot()
    actor = base.payload.actor.model_copy(update={"timeout_seconds": 1})
    payload = base.payload.model_copy(update={"actor": actor})
    snapshot = base.model_copy(
        update={
            "payload": payload,
            "digest": hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest(),
        }
    )
    plan = {
        "tasks": [
            {
                "id": "late-task",
                "member_id": "member-1",
                "objective": "inspect",
                "depends_on": [],
                "position": 0,
            }
        ]
    }
    class Factory:
        def __init__(self):
            self.supervisor_calls = 0
            self.member_calls = 0

        def build(self, member, **_kwargs):
            if member.agent_id == "supervisor":
                self.supervisor_calls += 1
                return TeamPlanGraph(plan)
            self.member_calls += 1
            return TextGraph("too late")

    request = _request(snapshot).model_copy(
        update={
            "deadline_at": datetime.now(UTC) + timedelta(minutes=5),
            "execution_deadline_at": datetime.now(UTC) + timedelta(minutes=5),
        }
    )
    gateway = FakeGateway(snapshot)
    factory = Factory()
    ticks = iter([0.0, 0.0, 0.0, 2.0])
    result = SandboxRuntime(
        gateway,
        agent_factory=factory,
        monotonic=lambda: next(ticks),
    ).execute(request)

    assert result.status == "failed"
    assert result.error_code == "sandbox_timeout"
    assert factory.supervisor_calls == 1
    assert factory.member_calls == 0


def test_team_timeout_interrupts_slow_planning_before_wider_run_deadline():
    base = _schema_v5_team_snapshot()
    actor = base.payload.actor.model_copy(update={"timeout_seconds": 1})
    payload = base.payload.model_copy(update={"actor": actor})
    snapshot = base.model_copy(
        update={
            "payload": payload,
            "digest": hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest(),
        }
    )
    plan = {
        "tasks": [
            {
                "id": "planned-task",
                "member_id": "member-1",
                "objective": "inspect",
                "depends_on": [],
                "position": 0,
            }
        ]
    }

    class SlowPlanGraph(TeamPlanGraph):
        def invoke(self, state, *, config=None):
            time.sleep(2)
            return super().invoke(state, config=config)

    class Factory:
        def build(self, member, **_kwargs):
            assert member.agent_id == "supervisor"
            return SlowPlanGraph(plan)

    gateway = FakeGateway(snapshot)
    request = _request(snapshot).model_copy(
        update={"deadline_at": datetime.now(UTC) + timedelta(minutes=5)}
    )
    started_at = time.monotonic()

    result = SandboxRuntime(gateway, agent_factory=Factory()).execute(request)

    assert result.status == "failed"
    assert result.error_code == "sandbox_timeout"
    assert time.monotonic() - started_at < 1.5
    assert gateway.completions[-1][0] == {
        "status": "failed",
        "error_code": "sandbox_timeout",
    }


def test_team_timeout_interrupts_slow_member_before_wider_run_deadline():
    base = _schema_v5_team_snapshot()
    actor = base.payload.actor.model_copy(update={"timeout_seconds": 1})
    payload = base.payload.model_copy(
        update={
            "actor": actor,
            "limits": base.payload.limits.model_copy(update={"max_iterations": 8}),
        }
    )
    snapshot = base.model_copy(
        update={
            "payload": payload,
            "digest": hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest(),
        }
    )
    plan = {
        "tasks": [
            {
                "id": "slow-task",
                "member_id": "member-1",
                "objective": "inspect",
                "depends_on": [],
                "position": 0,
            }
        ]
    }

    class Factory:
        def __init__(self):
            self.supervisor_calls = 0

        def build(self, member, **_kwargs):
            if member.agent_id == "supervisor":
                self.supervisor_calls += 1
                return TeamPlanGraph(plan)
            return TextGraph("late member result", delay=2)

    gateway = FakeGateway(snapshot)
    request = _request(snapshot).model_copy(
        update={"deadline_at": datetime.now(UTC) + timedelta(minutes=5)}
    )
    started_at = time.monotonic()

    result = SandboxRuntime(gateway, agent_factory=Factory()).execute(request)

    elapsed = time.monotonic() - started_at
    assert result.status == "failed"
    assert result.error_code == "sandbox_timeout"
    assert elapsed < 1.5
    assert gateway.completions[-1][0] == {
        "status": "failed",
        "error_code": "sandbox_timeout",
    }
    checkpoint_count = len(gateway.saved_checkpoints)
    event_count = len(gateway.events)
    time.sleep(1.2)
    assert len(gateway.saved_checkpoints) == checkpoint_count
    assert len(gateway.events) == event_count


def test_team_timeout_interrupts_slow_synthesis_before_wider_run_deadline():
    base = _schema_v5_team_snapshot()
    actor = base.payload.actor.model_copy(update={"timeout_seconds": 1})
    payload = base.payload.model_copy(
        update={
            "actor": actor,
            "limits": base.payload.limits.model_copy(update={"max_iterations": 8}),
        }
    )
    snapshot = base.model_copy(
        update={
            "payload": payload,
            "digest": hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest(),
        }
    )
    plan = {
        "tasks": [
            {
                "id": "fast-task",
                "member_id": "member-1",
                "objective": "inspect",
                "depends_on": [],
                "position": 0,
            }
        ]
    }

    class Factory:
        def __init__(self):
            self.supervisor_calls = 0

        def build(self, member, **_kwargs):
            if member.agent_id == "supervisor":
                self.supervisor_calls += 1
                if self.supervisor_calls == 1:
                    return TeamPlanGraph(plan)
                return TextGraph("late synthesis", delay=2)
            return TextGraph("member result")

    gateway = FakeGateway(snapshot)
    request = _request(snapshot).model_copy(
        update={"deadline_at": datetime.now(UTC) + timedelta(minutes=5)}
    )
    started_at = time.monotonic()

    result = SandboxRuntime(gateway, agent_factory=Factory()).execute(request)

    elapsed = time.monotonic() - started_at
    assert result.status == "failed"
    assert result.error_code == "sandbox_timeout"
    assert elapsed < 1.5
    assert not any(
        event["event_type"] == "team.synthesis.completed"
        for event in gateway.events
    )
    checkpoint_count = len(gateway.saved_checkpoints)
    event_count = len(gateway.events)
    time.sleep(1.2)
    assert len(gateway.saved_checkpoints) == checkpoint_count
    assert len(gateway.events) == event_count


def test_team_success_expiring_during_final_artifact_collection_reports_timeout():
    base = _schema_v5_team_snapshot(
        limits=_snapshot().payload.limits.model_copy(update={"max_iterations": 8})
    )
    actor = base.payload.actor.model_copy(update={"timeout_seconds": 1})
    payload = base.payload.model_copy(update={"actor": actor})
    snapshot = base.model_copy(
        update={
            "payload": payload,
            "digest": hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest(),
        }
    )
    plan = {
        "tasks": [
            {
                "id": "fast-task",
                "member_id": "member-1",
                "objective": "inspect",
                "depends_on": [],
                "position": 0,
            }
        ]
    }
    now = [0.0]

    class ExpiringArtifactGateway(FakeGateway):
        def list_artifacts(self):
            now[0] = 2.0
            return []

    class Factory:
        def __init__(self):
            self.supervisor_calls = 0

        def build(self, member, **_kwargs):
            if member.agent_id == "supervisor":
                self.supervisor_calls += 1
                if self.supervisor_calls == 1:
                    return TeamPlanGraph(plan)
                return TextGraph("synthesis")
            return TextGraph("member result")

    gateway = ExpiringArtifactGateway(snapshot)
    result = SandboxRuntime(
        gateway,
        agent_factory=Factory(),
        monotonic=lambda: now[0],
    ).execute(_request(snapshot))

    assert result.status == "failed"
    assert result.error_code == "sandbox_timeout"
    assert gateway.completions[-1][0] == {
        "status": "failed",
        "error_code": "sandbox_timeout",
    }
    assert not any(
        request[0].get("status") == "completed"
        for request in gateway.completions
    )


@pytest.mark.parametrize(
    "slow_stage",
    ["member", "synthesis"],
)
def test_team_deadline_expiry_before_wait_sets_late_checkpoint_fence(
    slow_stage,
):
    snapshot = _schema_v5_team_snapshot(
        limits=_snapshot().payload.limits.model_copy(update={"max_iterations": 8})
    )
    plan = {
        "tasks": [
            {
                "id": "fenced-task",
                "member_id": "member-1",
                "objective": "inspect",
                "depends_on": [],
                "position": 0,
            }
        ]
    }
    stage_entered = threading.Event()
    slow_stage_built = threading.Event()
    release_stage = threading.Event()
    stage_finished = threading.Event()

    class Graph:
        def __init__(self, stage):
            self.stage = stage

    class Factory:
        def __init__(self):
            self.supervisor_calls = 0

        def build(self, member, **_kwargs):
            if member.agent_id == "supervisor":
                self.supervisor_calls += 1
                graph = Graph(
                    "planning" if self.supervisor_calls == 1 else "synthesis"
                )
            else:
                graph = Graph("member")
            if graph.stage == slow_stage:
                slow_stage_built.set()
            return graph

    class CheckpointingAdapter:
        def __init__(self, graph, *, checkpoint_store=None):
            self.graph = graph
            self.checkpoint_store = checkpoint_store

        def invoke(self, _state, *, metadata=None):
            del metadata
            if self.graph.stage == "planning":
                return SimpleNamespace(content=json.dumps(plan))
            if self.graph.stage == slow_stage:
                stage_entered.set()
                assert release_stage.wait(5), "timed-out stage was not released"
                assert self.checkpoint_store is not None
                self.checkpoint_store.save(
                    "run-1",
                    "late-checkpoint",
                    {"stage": self.graph.stage},
                )
                stage_finished.set()
            return SimpleNamespace(content=f"{self.graph.stage} result")

    class ExpiringBeforeWaitRuntime(SandboxRuntime):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.expired = False

        def _remaining_team_seconds(self, deadline_at):
            if slow_stage_built.is_set() and not self.expired:
                self.expired = True
                raise _TeamTimedOut()
            return super()._remaining_team_seconds(deadline_at)

    gateway = FakeGateway(snapshot)
    runtime = ExpiringBeforeWaitRuntime(
        gateway,
        agent_factory=Factory(),
        runtime_adapter_type=CheckpointingAdapter,
    )

    result = runtime.execute(_request(snapshot))

    assert result.status == "failed"
    assert result.error_code == "sandbox_timeout"
    assert stage_entered.wait(2), "deadline stage did not enter"
    checkpoint_count = len(gateway.saved_checkpoints)
    release_stage.set()
    assert stage_finished.wait(2), "deadline stage did not finish"
    assert len(gateway.saved_checkpoints) == checkpoint_count


@pytest.mark.parametrize(
    ("cancel_on_snapshot_read", "completed_tasks"),
    [(2, 0), (3, 2)],
)
def test_team_runtime_checks_cancellation_before_dequeue_and_synthesis(
    cancel_on_snapshot_read,
    completed_tasks,
):
    snapshot = _schema_v5_team_snapshot(
        limits=_snapshot().payload.limits.model_copy(update={"max_iterations": 8})
    )
    plan = {
        "tasks": [
            {
                "id": "one",
                "member_id": "member-1",
                "objective": "inspect",
                "depends_on": [],
                "position": 0,
            },
            {
                "id": "two",
                "member_id": "member-2",
                "objective": "review",
                "depends_on": [],
                "position": 1,
            },
        ]
    }

    class CancellingGateway(FakeGateway):
        def get_snapshot(self):
            self.snapshot_reads += 1
            if self.snapshot_reads == cancel_on_snapshot_read:
                raise RunnerGatewayBusinessError("run_token_invalid")
            return self.snapshot

    class Factory:
        def __init__(self):
            self.supervisor_calls = 0

        def build(self, member, **kwargs):
            if member.agent_id == "supervisor":
                self.supervisor_calls += 1
                if self.supervisor_calls == 1:
                    return TeamPlanGraph(plan)
                return TextGraph("synthesis")
            return TextGraph(f"{member.agent_id}-result")

    gateway = CancellingGateway(snapshot)
    result = SandboxRuntime(gateway, agent_factory=Factory()).execute(
        _request(snapshot)
    )

    assert result.status == "cancelled"
    assert result.error_code == "sandbox_cancelled"
    assert sum(
        event["event_type"] == "team.task.completed"
        for event in gateway.events
    ) == completed_tasks
    assert not any(
        event["event_type"] == "team.synthesis.started"
        for event in gateway.events
    )


def test_team_approval_restart_restores_exact_task_events_and_shared_budget_state():
    base = _schema_v5_team_snapshot()
    payload = base.payload.model_copy(update={
        "limits": base.payload.limits.model_copy(
            update={"max_iterations": 6, "max_tool_calls": 4, "max_subagents": 2}
        )
    })
    snapshot = base.model_copy(update={
        "payload": payload,
        "digest": hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest(),
    })
    plan = {
        "tasks": [
            {
                "id": "first",
                "member_id": "member-1",
                "objective": "inspect",
                "depends_on": [],
                "position": 0,
            },
            {
                "id": "approval-task",
                "member_id": "member-2",
                "objective": "operate",
                "depends_on": ["first"],
                "position": 1,
            },
        ]
    }

    class DurableGateway(FakeGateway):
        def __init__(self, value):
            super().__init__(value)
            self.latest_checkpoint = None
            self.event_keys = set()
            self.approval_granted = False

        def get_latest_checkpoint(self):
            self.checkpoint_reads += 1
            if self.latest_checkpoint is None:
                raise RunnerGatewayBusinessError("checkpoint_not_found")
            return deepcopy(self.latest_checkpoint)

        def save_checkpoint(self, checkpoint_key, state, idempotency_key):
            saved = {
                "checkpoint_key": checkpoint_key,
                "snapshot_digest": self.snapshot.digest,
                "state": deepcopy(state),
            }
            self.saved_checkpoints.append(
                (checkpoint_key, deepcopy(state), idempotency_key)
            )
            self.latest_checkpoint = saved
            return deepcopy(saved)

        def append_event(self, **request):
            assert request["idempotency_key"] not in self.event_keys
            assert request["sequence"] == len(self.events) + 1
            self.event_keys.add(request["idempotency_key"])
            self.events.append(deepcopy(request))
            return request

        def invoke_model(self, request, idempotency_key):
            self.model_calls.append((deepcopy(request), idempotency_key))
            tool_calls = []
            if request["member_agent_id"] == "member-1":
                tool_calls = [
                    {"id": "tool-1", "name": "water.query", "arguments": {}},
                    {"id": "delegate-1", "name": "task", "arguments": {}},
                ]
            return {
                "content": f"{request['member_agent_id']}-result",
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
                "tool_calls": tool_calls,
            }

        def invoke_tool(self, **request):
            self.tool_calls.append(deepcopy(request))
            if not self.approval_granted:
                raise RunnerGatewayToolError(
                    "tool_approval_required",
                    approval_id="approval-1",
                )
            return {"approved": True}

    interrupted_states = []
    resumed_states = []

    class ModelGraph:
        def __init__(self, model, calls):
            self.model = model
            self.calls = calls

        def invoke(self, state, *, config=None):
            self.calls.append(deepcopy(state))
            response = self.model.invoke([HumanMessage(content="execute")])
            return {
                **state,
                "messages": [
                    *state["messages"],
                    {"role": "assistant", "content": response.content},
                ],
                "status": "completed",
            }

    class ApprovalAfterModelGraph:
        def __init__(self, model, tool, calls):
            self.model = model
            self.tool = tool
            self.calls = calls

        def invoke(self, state, *, config=None):
            self.calls.append(deepcopy(state))
            if state.get("member_progress") != "model-completed":
                self.model.invoke([HumanMessage(content="prepare operation")])
                state["member_progress"] = "model-completed"
            self.tool.run({}, tool_call_id="approval-tool-call")
            return {
                **state,
                "messages": [
                    *state["messages"],
                    {"role": "assistant", "content": "approved-result"},
                ],
                "status": "completed",
            }

    class Factory:
        def __init__(self, *, resumed):
            self.resumed = resumed
            self.supervisor_calls = 0
            self.built_members = []

        def build(self, member, **kwargs):
            self.built_members.append(member.agent_id)
            if member.agent_id == "supervisor":
                self.supervisor_calls += 1
                if not self.resumed and self.supervisor_calls == 1:
                    return TeamPlanGraph(plan)
                return ModelGraph(kwargs["model"], [])
            if member.agent_id == "member-1":
                return ModelGraph(kwargs["model"], [])
            return ApprovalAfterModelGraph(
                kwargs["model"],
                kwargs["tools"][0],
                resumed_states if self.resumed else interrupted_states,
            )

    gateway = DurableGateway(snapshot)
    first_factory = Factory(resumed=False)
    first = SandboxRuntime(gateway, agent_factory=first_factory).execute(
        _request(snapshot)
    )
    approval_checkpoint = deepcopy(gateway.latest_checkpoint["state"])

    assert first.status == "interrupted"
    assert approval_checkpoint["stage"] == "waiting_approval"
    assert approval_checkpoint["active_task_id"] == "approval-task"
    assert approval_checkpoint["active_member_agent_id"] == "member-2"
    assert approval_checkpoint["team_version_id"] == "version-1"
    assert approval_checkpoint["snapshot_digest"] == snapshot.digest
    assert approval_checkpoint["budget"] == {
        "next_invocation_sequence": 2,
        "tool_call_count": 2,
        "subagent_call_count": 1,
    }
    assert approval_checkpoint["event_sequence"] == len(gateway.events)

    persisted_invocation_id = "persisted-approval-invocation"
    gateway.latest_checkpoint["state"]["active_invocation_id"] = (
        persisted_invocation_id
    )
    gateway.latest_checkpoint["state"]["active_invocations"][0][
        "invocation_id"
    ] = persisted_invocation_id
    gateway.latest_checkpoint["state"]["active_invocations"][0][
        "runtime_state"
    ]["team_task_invocation_id"] = persisted_invocation_id

    gateway.approval_granted = True
    second_factory = Factory(resumed=True)
    second = SandboxRuntime(gateway, agent_factory=second_factory).execute(
        _request(snapshot)
    )

    assert second.status == "completed"
    assert "member-1" not in second_factory.built_members
    assert [key for _, key in gateway.model_calls] == [
        "model-member-1-0",
        "model-member-2-1",
        "model-supervisor-2",
    ]
    assert sum(
        request["member_agent_id"] == "member-2"
        for request, _ in gateway.model_calls
    ) == 1
    assert len(gateway.tool_calls) == 2
    assert gateway.tool_calls[0]["tool_call_id"] == (
        "team:version-1:approval-task:approval-tool-call"
    )
    assert gateway.tool_calls[1]["tool_call_id"] == (
        "persisted-approval-invocation:approval-tool-call"
    )
    assert gateway.tool_calls[0]["member_agent_id"] == "member-2"
    assert resumed_states[0]["team_task_invocation_id"] == persisted_invocation_id
    approval_registrations = [
        registration
        for registration in gateway.artifact_capability_registrations
        if registration["task_id"] == "approval-task"
    ]
    assert approval_registrations[-1]["invocation_id"] == persisted_invocation_id
    assert resumed_states[0]["member_progress"] == "model-completed"
    event_types = [event["event_type"] for event in gateway.events]
    assert event_types.count("runner.started") == 1
    assert event_types.count("team.plan.created") == 1
    assert event_types.count("team.task.started") == 2
    assert event_types.count("team.task.completed") == 2
    assert event_types.count("approval.required") == 1
    assert [event["sequence"] for event in gateway.events] == list(
        range(1, len(gateway.events) + 1)
    )
    final_checkpoint = deepcopy(gateway.latest_checkpoint["state"])
    events_before_restart = deepcopy(gateway.events)
    model_call_count_before_restart = len(gateway.model_calls)

    assert final_checkpoint["stage"] == "completed"
    assert final_checkpoint["event_sequence"] == len(events_before_restart)

    completed_restart = SandboxRuntime(
        gateway, agent_factory=Factory(resumed=True)
    ).execute(_request(snapshot))

    assert completed_restart.status == "completed"
    assert gateway.events == events_before_restart
    assert len(gateway.model_calls) == model_call_count_before_restart


def test_team_restart_fails_closed_for_an_inflight_task_without_runtime_checkpoint():
    snapshot = _schema_v5_team_snapshot()
    plan = TeamPlan(tasks=(
        TeamTask(
            id="approval-task",
            member_id="member-2",
            objective="operate gate",
            position=0,
        ),
    ))
    checkpoint = TeamSchedulerState(
        stage="executing",
        snapshot_digest=snapshot.digest,
        team_version_id="version-1",
        plan=plan,
        pending_task_ids=("approval-task",),
        started_task_ids=("approval-task",),
        active_task_id="approval-task",
        active_member_agent_id="member-2",
        active_invocation_id="team:version-1:approval-task",
        budget=TeamBudgetState(
            next_invocation_sequence=1,
            tool_call_count=0,
            subagent_call_count=0,
        ),
    ).model_dump(mode="json")

    class DurableGateway(FakeGateway):
        def __init__(self):
            super().__init__(snapshot)
            self.latest_checkpoint = {
                "checkpoint_key": "team-scheduler",
                "snapshot_digest": snapshot.digest,
                "state": checkpoint,
            }

        def get_latest_checkpoint(self):
            return deepcopy(self.latest_checkpoint)

    class RejectingFactory:
        def __init__(self):
            self.build_calls = 0

        def build(self, *_args, **_kwargs):
            self.build_calls += 1
            raise AssertionError("unsafe in-flight task was replayed")

    gateway = DurableGateway()
    factory = RejectingFactory()
    result = SandboxRuntime(gateway, agent_factory=factory).execute(
        _request(snapshot)
    )

    assert result.status == "failed"
    assert result.error_code == "team_recovery_required"
    assert factory.build_calls == 0
    assert gateway.model_calls == []
    assert gateway.tool_calls == []


def test_team_parallel_approval_interruptions_persist_every_active_invocation():
    snapshot = _schema_v5_team_snapshot()
    plan = {
        "tasks": [
            {
                "id": "member-1-task",
                "member_id": "member-1",
                "objective": "inspect",
                "depends_on": [],
                "position": 0,
            },
            {
                "id": "member-2-task",
                "member_id": "member-2",
                "objective": "operate",
                "depends_on": [],
                "position": 1,
            },
        ]
    }

    class DurableGateway(FakeGateway):
        def __init__(self):
            super().__init__(snapshot)
            self.latest_checkpoint = None

        def get_latest_checkpoint(self):
            if self.latest_checkpoint is None:
                raise RunnerGatewayBusinessError("checkpoint_not_found")
            return deepcopy(self.latest_checkpoint)

        def save_checkpoint(self, checkpoint_key, state, idempotency_key):
            self.latest_checkpoint = {
                "checkpoint_key": checkpoint_key,
                "snapshot_digest": snapshot.digest,
                "state": deepcopy(state),
            }
            return deepcopy(self.latest_checkpoint)

        def invoke_tool(self, **request):
            self.tool_calls.append(deepcopy(request))
            raise RunnerGatewayToolError(
                "tool_approval_required",
                approval_id=f"approval-{request['member_agent_id']}",
            )

    class ApprovalGraph:
        def __init__(self, member_id, tool):
            self.member_id = member_id
            self.tool = tool

        def invoke(self, state, *, config=None):
            state["member_progress"] = "awaiting-approval"
            self.tool.run({}, tool_call_id=f"{self.member_id}-tool")
            raise AssertionError("approval tool unexpectedly returned")

    class Factory:
        def build(self, member, **kwargs):
            if member.agent_id == "supervisor":
                return TeamPlanGraph(plan)
            return ApprovalGraph(member.agent_id, kwargs["tools"][0])

    gateway = DurableGateway()
    result = SandboxRuntime(gateway, agent_factory=Factory()).execute(
        _request(snapshot)
    )

    assert result.status == "interrupted"
    active = gateway.latest_checkpoint["state"]["active_invocations"]
    assert {item["task_id"] for item in active} == {
        "member-1-task",
        "member-2-task",
    }
    assert {item["approval_id"] for item in active} == {
        "approval-member-1",
        "approval-member-2",
    }
    assert all(item["checkpoint_status"] == "interrupted" for item in active)
    assert all(
        item["runtime_state"]["member_progress"] == "awaiting-approval"
        for item in active
    )


def test_team_runtime_rejects_member_output_above_its_task_contract():
    snapshot = _schema_v5_team_snapshot()
    plan = {
        "tasks": [
            {
                "id": "bounded-task",
                "member_id": "member-1",
                "objective": "inspect",
                "depends_on": [],
                "position": 0,
                "output_contract": {"max_output_bytes": 4},
            },
        ]
    }

    class Factory:
        def __init__(self):
            self.supervisor_calls = 0

        def build(self, member, **_kwargs):
            if member.agent_id == "supervisor":
                self.supervisor_calls += 1
                if self.supervisor_calls == 1:
                    return TeamPlanGraph(plan)
                return TextGraph("partial synthesis")
            return TextGraph("too large")

    gateway = FakeGateway(snapshot)
    result = SandboxRuntime(gateway, agent_factory=Factory()).execute(
        _request(snapshot)
    )

    assert result.status == "failed"
    assert result.error_code == "runtime_output_limit"
    assert any(
        event["event_type"] == "team.task.failed"
        and event["payload"] == {
            "team_id": "team-1",
            "version_id": "version-1",
            "agent_id": "member-1",
            "task_id": "bounded-task",
            "position": 0,
            "error_code": "runtime_output_limit",
        }
        for event in gateway.events
    )
    assert not any(
        event["event_type"] == "team.task.completed"
        and event["payload"]["task_id"] == "bounded-task"
        for event in gateway.events
    )


def test_langgraph_approval_restart_uses_interrupted_node_state_without_replaying_prior_node():
    from typing import TypedDict

    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

    class WorkflowState(TypedDict, total=False):
        prepared: bool
        messages: list[dict]

    class Store:
        state = None

        def load_latest(self, _run_id):
            return self.state

        def save(self, _run_id, _key, state):
            self.state = deepcopy(state)

    prepared_calls = []

    def build_graph():
        graph = StateGraph(WorkflowState)
        graph.add_node("prepare", lambda _state: (prepared_calls.append("prepare") or {"prepared": True}))
        graph.add_node("approval", lambda _state: (_ for _ in ()).throw(RunnerApprovalInterruption("approval-1")))
        graph.add_conditional_edges(START, lambda state: "approval" if state.get("prepared") else "prepare")
        graph.add_edge("prepare", "approval")
        graph.add_edge("approval", END)
        return graph.compile(checkpointer=MemorySaver())

    store = Store()
    with pytest.raises(RunnerApprovalInterruption):
        LangGraphRuntimeAdapter(build_graph(), checkpoint_store=store).invoke(
            RuntimeState(run_id="run-1", messages=[])
        )
    assert store.state["prepared"] is True

    with pytest.raises(RunnerApprovalInterruption):
        LangGraphRuntimeAdapter(build_graph(), checkpoint_store=store).invoke(
            RuntimeState(run_id="run-1", messages=[])
        )
    assert prepared_calls == ["prepare"]


def test_team_synthesis_approval_restart_preserves_exact_graph_frontier():
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    class WorkflowState(TypedDict, total=False):
        messages: list[dict]
        prepared: bool

    snapshot = _schema_v5_team_snapshot()
    gateway = DurableApprovalGateway(snapshot)
    member_calls = []
    synthesis_prepared_calls = []
    plan = {
        "tasks": [
            {
                "id": "member-task",
                "member_id": "member-1",
                "objective": "inspect",
                "depends_on": [],
                "position": 0,
            }
        ]
    }

    class Factory:
        def build(self, member, **kwargs):
            if member.agent_id == "member-1":
                return TextGraph("member result", calls=member_calls)
            if kwargs["tools"] == []:
                return TeamPlanGraph(plan)

            tool = kwargs["tools"][0]
            graph = StateGraph(WorkflowState)

            def prepare(_state):
                synthesis_prepared_calls.append("prepare")
                return {"prepared": True}

            def approval(state):
                tool.run({}, tool_call_id="synthesis-approval-tool-call")
                return {
                    "messages": [
                        *state["messages"],
                        {"role": "assistant", "content": "team summary"},
                    ]
                }

            graph.add_node("prepare", prepare)
            graph.add_node("approval", approval)
            graph.add_edge(START, "prepare")
            graph.add_edge("prepare", "approval")
            graph.add_edge("approval", END)
            return graph.compile(checkpointer=kwargs["checkpointer"])

    interrupted = SandboxRuntime(gateway, agent_factory=Factory()).execute(
        _request(snapshot)
    )

    assert interrupted.status == "interrupted"

    gateway.approval_granted = True
    resumed = SandboxRuntime(gateway, agent_factory=Factory()).execute(
        _request(snapshot)
    )

    assert resumed.status == "completed"
    assert len(member_calls) == 1
    assert synthesis_prepared_calls == ["prepare"]
    synthesis_tool_calls = [
        request
        for request in gateway.tool_calls
        if request["tool_call_id"].endswith(":synthesis-approval-tool-call")
    ]
    assert len(synthesis_tool_calls) == 2
    assert {
        request["tool_call_id"] for request in synthesis_tool_calls
    } == {"team:version-1:synthesis:synthesis-approval-tool-call"}
    assert synthesis_tool_calls[0] == synthesis_tool_calls[1]
    assert [event["sequence"] for event in gateway.events] == list(
        range(1, len(gateway.events) + 1)
    )
    assert [event["event_type"] for event in gateway.events].count(
        "team.synthesis.started"
    ) == 1
    assert [event["event_type"] for event in gateway.events].count(
        "team.task.started"
    ) == 1


@pytest.mark.parametrize(
    ("status", "error_code", "expected"),
    [
        ("completed", None, 0),
        ("interrupted", "approval_required", 0),
        ("failed", "sandbox_failed", 1),
        ("failed", "sandbox_timeout", 4),
        ("cancelled", "sandbox_cancelled", 3),
    ],
)
def test_worker_returns_fixed_exit_codes(
    monkeypatch, status, error_code, expected
):
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
            return SimpleNamespace(status=status, error_code=error_code)

    monkeypatch.setattr(run_worker.sys, "argv", ["run_worker"])
    monkeypatch.setattr(run_worker, "load_execution_request", lambda: request)
    monkeypatch.setattr(run_worker, "RunnerGatewayClient", ClientFactory)
    monkeypatch.setattr(run_worker, "SandboxRuntime", Runtime)

    assert run_worker.main() == expected


def test_worker_timeout_exit_survives_failed_completion_report(monkeypatch):
    snapshot = _snapshot()
    request = _request(snapshot).model_copy(
        update={
            "execution_deadline_at": datetime.now(UTC) - timedelta(seconds=1)
        }
    )

    class CompletionFailingGateway(FakeGateway):
        def __init__(self, current_snapshot):
            super().__init__(current_snapshot)
            self.completion_attempts = 0

        def complete(self, request, idempotency_key):
            self.completion_attempts += 1
            raise RunnerGatewayBusinessError("runner_gateway_unavailable")

    gateway = CompletionFailingGateway(snapshot)

    class ClientFactory:
        @staticmethod
        def from_execution_request(_request):
            return gateway

    monkeypatch.setattr(run_worker.sys, "argv", ["run_worker"])
    monkeypatch.setattr(run_worker, "load_execution_request", lambda: request)
    monkeypatch.setattr(run_worker, "RunnerGatewayClient", ClientFactory)

    assert run_worker.main() == 4
    assert gateway.snapshot_reads == 0
    assert gateway.completion_attempts == 1
    assert gateway.completions == []


@pytest.mark.parametrize("slow_stage", ["planning", "member", "synthesis"])
def test_real_worker_process_does_not_join_abandoned_team_deadline_work(
    slow_stage,
    tmp_path,
):
    snapshot = _schema_v5_team_snapshot()
    request = _request(snapshot)
    ready_path = tmp_path / f"{slow_stage}.ready"
    snapshot_path = tmp_path / f"{slow_stage}.snapshot.json"
    request_path = tmp_path / f"{slow_stage}.request.json"
    snapshot_path.write_text(snapshot.model_dump_json(), encoding="utf-8")
    request_path.write_text(request.model_dump_json(), encoding="utf-8")
    probe_path = Path(__file__).with_name("worker_deadline_probe.py")
    process = subprocess.Popen(
        [
            sys.executable,
            str(probe_path),
            slow_stage,
            str(ready_path),
            str(snapshot_path),
            str(request_path),
        ],
        cwd=Path(__file__).resolve().parents[2],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready_deadline = time.monotonic() + 10
        while not ready_path.exists() and process.poll() is None:
            if time.monotonic() >= ready_deadline:
                pytest.fail("worker did not enter the selected slow stage")
            time.sleep(0.01)
        assert ready_path.read_text(encoding="utf-8") == slow_stage
        entered_at = time.monotonic()
        stdout, stderr = process.communicate(timeout=2.5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert process.returncode == 4, (stdout, stderr)
    assert time.monotonic() - entered_at < 2.5


def test_worker_rejects_invalid_execution_envelope(monkeypatch):
    monkeypatch.setattr(run_worker.sys, "argv", ["run_worker"])
    monkeypatch.setattr(
        run_worker,
        "load_execution_request",
        lambda: (_ for _ in ()).throw(KeyError("missing")),
    )

    assert run_worker.main() == 2
