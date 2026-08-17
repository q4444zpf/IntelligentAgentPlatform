from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.core.request_context import RequestContext
from app.tools.gateway import ToolGateway
from app.tools.schemas import ToolCall, ToolExecutionContext


@dataclass
class ToolGatewayAdapter:
    """Expose one published platform tool to a LangChain/Deep Agents runtime."""

    gateway: ToolGateway
    tool_id: str
    description: str
    input_schema: dict[str, Any]
    context: ToolExecutionContext

    @property
    def name(self) -> str:
        return self.tool_id

    def invoke(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise RuntimeError("工具参数无效。")
        call = ToolCall(
            id=f"deepagents-{uuid4().hex}",
            name=self.tool_id,
            arguments=arguments,
        )
        try:
            result = self.gateway.execute(call, self.context, {self.tool_id})
        except Exception as error:
            safe_message = getattr(error, "safe_message", "工具执行失败。")
            raise RuntimeError(safe_message) from error
        return result.value


def build_gateway_tools(
    gateway: ToolGateway,
    tools: list[tuple[str, str, dict[str, Any]]],
    context: ToolExecutionContext,
) -> list[ToolGatewayAdapter]:
    return [
        ToolGatewayAdapter(gateway, tool_id, description, schema, context)
        for tool_id, description, schema in tools
    ]
