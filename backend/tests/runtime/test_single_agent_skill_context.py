import hashlib
from datetime import UTC, datetime, timedelta

from app.runtime.execution_contract import RunExecutionRequest
from app.runtime.execution_snapshot import (
    ExecutionSnapshotPayload,
    PublishedAgentSnapshot,
    SnapshotMessage,
    SnapshotModelSelection,
    SnapshotRuntimeLimits,
    SnapshotSkill,
    SnapshotTool,
    canonical_snapshot_bytes,
)
from app.runtime.runner_gateway_client import RunnerGatewayBusinessError
from app.runtime.runner_gateway_schemas import SnapshotResponse
from app.runtime.sandbox_runtime import SandboxRuntime


class Gateway:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.tool_calls = []
        self.completions = []
        self.model_requests = []

    def get_snapshot(self):
        return self.snapshot

    def get_latest_checkpoint(self):
        raise RunnerGatewayBusinessError("checkpoint_not_found")

    def append_event(self, **request):
        return request

    def save_checkpoint(self, checkpoint_key, state, idempotency_key):
        return {
            "checkpoint_key": checkpoint_key,
            "snapshot_digest": self.snapshot.digest,
            "state": state,
        }

    def complete(self, request, idempotency_key):
        self.completions.append((request, idempotency_key))
        return request

    def invoke_tool(self, **request):
        self.tool_calls.append(request)
        return {"water_level": 3.2}

    def invoke_model(self, request, idempotency_key):
        self.model_requests.append((request, idempotency_key))
        if len(self.model_requests) == 1:
            return {
                "content": None,
                "tool_calls": [{
                    "id": "model-call-1",
                    "name": "water.query",
                    "arguments": {},
                }],
            }
        return {"content": "final answer", "tool_calls": []}

    def list_artifacts(self):
        return []


def test_sandbox_skill_context_exposes_gateway_tools_and_returns_final_answer():
    payload = ExecutionSnapshotPayload(
        schema_version="3",
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
        messages=(SnapshotMessage(
            id="message-1",
            sequence=1,
            role="user",
            content="execute",
            created_at=datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
        ),),
        skills=(SnapshotSkill(
            name="forecast",
            content="Use the bound forecast tool before answering.",
        ),),
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
            SnapshotTool(
                tool_id="system.get_current_time",
                version="1",
                name="Current time",
                description="Read the platform time",
                input_schema={"type": "object", "properties": {}},
                published=True,
                enabled=True,
                source_available=True,
            ),
        ),
        limits=SnapshotRuntimeLimits(snapshot_max_bytes=1048576),
        created_at=datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
    )
    snapshot = SnapshotResponse(
        snapshot_id=payload.snapshot_id,
        run_id=payload.run_id,
        digest=hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest(),
        payload=payload,
    )
    deadline = datetime.now(UTC) + timedelta(minutes=5)
    request = RunExecutionRequest(
        run_id="run-1",
        agent_version="agent-v1",
        checkpoint_key="checkpoint-1",
        deadline_at=deadline,
        execution_deadline_at=deadline,
        snapshot_id=snapshot.snapshot_id,
        snapshot_digest=snapshot.digest,
        gateway_url="http://api:8000/internal/runner",
        run_token="secret-token",
    )
    gateway = Gateway(snapshot)

    result = SandboxRuntime(gateway).execute(request)

    assert result.status == "completed"
    first_request, first_key = gateway.model_requests[0]
    assert first_key == "model-0"
    assert any(
        "Use the bound forecast tool before answering." in message["content"]
        for message in first_request["messages"]
        if message["role"] == "system"
    )
    model_tools = {tool["tool_id"]: tool for tool in first_request["tools"]}
    assert {"water.query", "system.get_current_time"} <= set(model_tools)
    assert model_tools["water.query"]["input_schema"] == {
        "type": "object", "properties": {}
    }
    assert model_tools["system.get_current_time"]["input_schema"] == {
        "type": "object", "properties": {}
    }
    assert gateway.tool_calls == [{
        "tool_id": "water.query",
        "version": "1",
        "tool_call_id": "model-call-1",
        "arguments": {},
        "invocation_sequence": 0,
        "idempotency_key": "tool:model-call-1:0",
    }]
    assert gateway.completions[-1][0]["final_assistant_content"] == "final answer"
