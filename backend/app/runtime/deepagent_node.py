from __future__ import annotations

from typing import Any, Protocol


class AgentInvoker(Protocol):
    def invoke(self, state: dict[str, Any], *, config: dict[str, Any] | None = None) -> dict[str, Any]: ...


class DeepAgentNode:
    """LangGraph node wrapper for a Deep Agent instance."""

    def __init__(self, agent: AgentInvoker):
        self.agent = agent

    def __call__(self, state: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
        messages = state.get("messages")
        if not isinstance(messages, list):
            raise RuntimeError("Invalid runtime messages")
        output = self.agent.invoke({"messages": list(messages)}, config=config)
        result_messages = output.get("messages") if isinstance(output, dict) else None
        if not isinstance(result_messages, list):
            raise RuntimeError("Deep Agent did not produce messages")
        return {
            **state,
            "messages": result_messages,
            "status": "completed",
        }
