from __future__ import annotations

from itertools import count
from typing import Any, Protocol

from langchain_core.tools import StructuredTool

from .execution_snapshot import ExecutionSnapshotPayload, SnapshotTool

_SAFE_MESSAGES = {
    "tool_not_authorized": "该工具当前不可用。",
    "tool_invalid_arguments": "工具参数无效。",
    "tool_duplicate_call": "工具调用标识重复。",
    "tool_execution_failed": "工具执行失败。",
    "tool_approval_required": "该工具需要人工审批后才能执行。",
    "gateway_unavailable": "Runner Gateway 不可用。",
}


class RunnerGatewayToolError(RuntimeError):
    def __init__(self, code: str, *, approval_id: str | None = None) -> None:
        self.code = code if code in _SAFE_MESSAGES else "gateway_unavailable"
        self.approval_id = approval_id
        super().__init__(_SAFE_MESSAGES[self.code])


class RunnerApprovalInterruption(RuntimeError):
    def __init__(self, approval_id: str) -> None:
        self.approval_id = approval_id
        super().__init__("该工具需要人工审批后才能执行。")


class RunnerToolClient(Protocol):
    def invoke_tool(self, **request: Any) -> dict[str, Any]: ...


class GatewayStructuredTool(StructuredTool):
    def _to_args_and_kwargs(self, tool_input, tool_call_id):
        args, kwargs = super()._to_args_and_kwargs(tool_input, tool_call_id)
        kwargs["_tool_call_id"] = tool_call_id
        return args, kwargs


def build_gateway_tools(
    snapshot: ExecutionSnapshotPayload,
    client: RunnerToolClient,
) -> list[StructuredTool]:
    return [
        _build_tool(tool, client)
        for tool in snapshot.tools
        if tool.published and tool.enabled and tool.source_available
    ]


def _build_tool(
    tool: SnapshotTool,
    client: RunnerToolClient,
) -> StructuredTool:
    sequences = count()

    def invoke_gateway(_tool_call_id: str | None = None, **arguments: Any):
        if not _tool_call_id:
            raise RunnerGatewayToolError("tool_execution_failed")
        sequence = next(sequences)
        try:
            return client.invoke_tool(
                tool_id=tool.tool_id,
                version=tool.version,
                tool_call_id=_tool_call_id,
                arguments=arguments,
                invocation_sequence=sequence,
                idempotency_key=f"tool:{_tool_call_id}:{sequence}",
            )
        except RunnerGatewayToolError as error:
            if (
                error.code == "tool_approval_required"
                and error.approval_id is not None
            ):
                raise RunnerApprovalInterruption(error.approval_id) from error
            raise RunnerGatewayToolError(error.code) from error

    return GatewayStructuredTool.from_function(
        func=invoke_gateway,
        name=tool.tool_id,
        description=tool.description or tool.name,
        args_schema=tool.input_schema,
        infer_schema=False,
    )
