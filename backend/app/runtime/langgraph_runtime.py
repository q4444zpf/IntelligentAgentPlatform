from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class InvokableGraph(Protocol):
    def invoke(self, state: dict[str, Any], *, config: dict[str, Any] | None = None) -> dict[str, Any]: ...


@dataclass
class RuntimeState:
    run_id: str
    messages: list[dict[str, Any]]
    status: str = "queued"
    values: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "messages": list(self.messages),
            "status": self.status,
            **self.values,
        }


@dataclass(frozen=True)
class RuntimeResult:
    status: str
    content: str
    state: dict[str, Any]


class LangGraphRuntimeAdapter:
    """Stable platform boundary around a LangGraph compiled graph.

    The adapter deliberately owns only run-scoped state and config. Tool
    authorization, approvals, persistence, and artifact writes remain in
    platform services around the graph.
    """

    def __init__(self, graph: InvokableGraph, checkpoint_store=None):
        self.graph = graph
        self.checkpoint_store = checkpoint_store

    def invoke(self, state: RuntimeState, *, metadata: dict[str, Any] | None = None) -> RuntimeResult:
        initial_state = state.as_dict()
        if self.checkpoint_store is not None:
            restored = self.checkpoint_store.load_latest(state.run_id)
            if isinstance(restored, dict):
                initial_state = {**initial_state, **restored}
        config = {
            "configurable": {"thread_id": state.run_id},
            "metadata": dict(metadata or {}),
        }
        output = self.graph.invoke(initial_state, config=config)
        messages = output.get("messages") if isinstance(output, dict) else None
        if not isinstance(messages, list):
            raise RuntimeError("Graph did not produce an assistant result")
        assistant_messages = [
            message for message in messages
            if isinstance(message, dict)
            and message.get("role") == "assistant"
            and isinstance(message.get("content"), str)
            and message["content"].strip()
        ]
        if not assistant_messages:
            raise RuntimeError("Graph did not produce an assistant result")
        if self.checkpoint_store is not None:
            self.checkpoint_store.save(state.run_id, "langgraph", output)
        return RuntimeResult(
            status=str(output.get("status", "completed")),
            content=assistant_messages[-1]["content"],
            state=output,
        )
