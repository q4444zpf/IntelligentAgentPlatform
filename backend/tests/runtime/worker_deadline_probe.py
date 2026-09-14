from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_ROOT))


class _UnusedBackendResult:
    def __init__(self, **values):
        self.__dict__.update(values)


def _unused_create_file_data(_content):
    raise AssertionError("probe graphs must not access artifact files")


deepagents = ModuleType("deepagents")
deepagents.__path__ = []
deepagents_backends = ModuleType("deepagents.backends")
deepagents_backends.__path__ = []
deepagents_protocol = ModuleType("deepagents.backends.protocol")
for protocol_name in (
    "BackendProtocol",
    "DeleteResult",
    "EditResult",
    "FileDownloadResponse",
    "FileUploadResponse",
    "GlobResult",
    "GrepResult",
    "LsResult",
    "ReadResult",
    "WriteResult",
):
    setattr(deepagents_protocol, protocol_name, _UnusedBackendResult)
deepagents_utils = ModuleType("deepagents.backends.utils")
deepagents_utils.create_file_data = _unused_create_file_data
deepagents.backends = deepagents_backends
deepagents_backends.protocol = deepagents_protocol
deepagents_backends.utils = deepagents_utils
sys.modules["deepagents"] = deepagents
sys.modules["deepagents.backends"] = deepagents_backends
sys.modules["deepagents.backends.protocol"] = deepagents_protocol
sys.modules["deepagents.backends.utils"] = deepagents_utils


class _ProbeGatewayModel:
    def __init__(self, *_args, **_kwargs):
        pass


class _ProbeGatewayModelBudget:
    def __init__(
        self,
        *,
        max_iterations,
        max_tool_calls,
        max_subagents,
        next_invocation_sequence=0,
        tool_call_count=0,
        subagent_call_count=0,
    ):
        self.max_iterations = max_iterations
        self.max_tool_calls = max_tool_calls
        self.max_subagents = max_subagents
        self.next_invocation_sequence = next_invocation_sequence
        self.tool_call_count = tool_call_count
        self.subagent_call_count = subagent_call_count


class _ProbeGatewayModelError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _ProbeApprovalInterruption(RuntimeError):
    def __init__(self, approval_id):
        self.approval_id = approval_id
        super().__init__(approval_id)


def _build_no_gateway_tools(*_args, **_kwargs):
    return []


gateway_model = ModuleType("app.runtime.gateway_model")
gateway_model.GatewayChatModel = _ProbeGatewayModel
gateway_model.GatewayModelBudget = _ProbeGatewayModelBudget
gateway_model.RunnerGatewayModelError = _ProbeGatewayModelError
gateway_tools = ModuleType("app.runtime.gateway_tools")
gateway_tools.RunnerApprovalInterruption = _ProbeApprovalInterruption
gateway_tools.build_gateway_tools = _build_no_gateway_tools
gateway_tools.build_skill_resource_tools = _build_no_gateway_tools
gateway_tools.build_skill_script_tools = _build_no_gateway_tools
sys.modules["app.runtime.gateway_model"] = gateway_model
sys.modules["app.runtime.gateway_tools"] = gateway_tools

from app.runtime import run_worker
from app.runtime.deepagents_factory import (
    create_in_memory_checkpointer,
)
from app.runtime.execution_contract import RunExecutionRequest
from app.runtime.runner_gateway_client import (
    RunnerGatewayBusinessError,
)
from app.runtime.runner_gateway_schemas import SnapshotResponse
from app.runtime.sandbox_runtime import SandboxRuntime


class ProbeGateway:
    def __init__(self, snapshot: SnapshotResponse):
        self.snapshot = snapshot

    def get_snapshot(self):
        return self.snapshot

    def get_latest_checkpoint(self):
        raise RunnerGatewayBusinessError("checkpoint_not_found")

    def save_checkpoint(self, checkpoint_key, state, idempotency_key):
        return {
            "checkpoint_key": checkpoint_key,
            "snapshot_digest": self.snapshot.digest,
            "state": state,
        }

    def append_event(self, **request):
        return request

    def complete(self, request, idempotency_key):
        return request

    def invoke_model(self, request, idempotency_key):
        raise AssertionError("probe graphs must not call the gateway model")

    def invoke_tool(self, **request):
        raise AssertionError("probe graphs must not invoke tools")

    def register_artifact_capability(self, **request):
        return f"capability:{request['invocation_id']}"

    def list_artifacts(self):
        return []


class ProbeTextGraph:
    def __init__(self, content: str):
        self.content = content

    def invoke(self, state, *, config=None):
        return {
            **state,
            "messages": [
                *state["messages"],
                {"role": "assistant", "content": self.content},
            ],
            "status": "completed",
        }


class ProbePlanGraph:
    def __init__(self, plan: dict):
        self.plan = plan

    def invoke(self, state, *, config=None):
        return ProbeTextGraph(json.dumps(self.plan)).invoke(state, config=config)


class SlowGraph:
    def __init__(self, graph, stage: str, ready_path: Path):
        self.graph = graph
        self.stage = stage
        self.ready_path = ready_path

    def invoke(self, state, *, config=None):
        pending_path = self.ready_path.with_suffix(f"{self.ready_path.suffix}.pending")
        pending_path.write_text(self.stage, encoding="utf-8")
        pending_path.replace(self.ready_path)
        time.sleep(5)
        return self.graph.invoke(state, config=config)


class ProbeAgentFactory:
    def __init__(self, stage: str, ready_path: Path):
        self.stage = stage
        self.ready_path = ready_path
        self.supervisor_calls = 0
        self.plan = {
            "tasks": [
                {
                    "id": "probe-task",
                    "member_id": "member-1",
                    "objective": "probe",
                    "depends_on": [],
                    "position": 0,
                }
            ]
        }

    def slow(self, graph):
        return SlowGraph(graph, self.stage, self.ready_path)

    def build(self, member, **_kwargs):
        if member.agent_id == "supervisor":
            self.supervisor_calls += 1
            if self.stage == "planning" and self.supervisor_calls == 1:
                return self.slow(ProbeTextGraph(json.dumps(self.plan)))
            if self.supervisor_calls == 1:
                return ProbePlanGraph(self.plan)
            if self.stage == "synthesis":
                return self.slow(ProbeTextGraph("late synthesis"))
            return ProbeTextGraph("synthesis")
        graph = ProbeTextGraph("member")
        if self.stage == "member" and member.agent_id == "member-1":
            return self.slow(graph)
        return graph


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 4:
        raise SystemExit(
            "usage: worker_deadline_probe.py "
            "<stage> <ready-path> <snapshot-path> <request-path>"
        )
    stage, ready_name, snapshot_name, request_name = arguments
    if stage not in {"planning", "member", "synthesis"}:
        raise SystemExit(f"unsupported stage: {stage}")
    ready_path = Path(ready_name)
    snapshot = SnapshotResponse.model_validate_json(
        Path(snapshot_name).read_text(encoding="utf-8")
    )
    request = RunExecutionRequest.model_validate_json(
        Path(request_name).read_text(encoding="utf-8")
    )
    gateway = ProbeGateway(snapshot)

    class ClientFactory:
        @staticmethod
        def from_execution_request(_request):
            return gateway

    run_worker.load_execution_request = lambda: request
    run_worker.RunnerGatewayClient = ClientFactory
    run_worker.SandboxRuntime = lambda current_gateway: SandboxRuntime(
        current_gateway,
        agent_factory=ProbeAgentFactory(stage, ready_path),
    )
    run_worker.sys.argv = ["run_worker"]
    create_in_memory_checkpointer()
    now = datetime.now(UTC)
    request = request.model_copy(
        update={
            "deadline_at": now + timedelta(seconds=5),
            "execution_deadline_at": now + timedelta(seconds=0.75),
        }
    )
    return run_worker.main()


if __name__ == "__main__":
    raise SystemExit(main())
