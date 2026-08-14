import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.runtime import run_worker
from app.runtime.execution_contract import RunExecutionRequest
from app.runtime.execution_snapshot import (
    ExecutionSnapshotPayload,
    PublishedAgentSnapshot,
    SnapshotMessage,
    SnapshotModelSelection,
    SnapshotRuntimeLimits,
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
