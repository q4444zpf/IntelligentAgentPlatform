from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from .gateway_tools import RunnerApprovalInterruption


class InvokableGraph(Protocol):
    def invoke(
        self,
        state: dict[str, Any] | None,
        *,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


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

    _CHECKPOINT_BUNDLE_KEY = "__langgraph_checkpoint__"
    _CHECKPOINT_BUNDLE_VERSION = 1

    def __init__(self, graph: InvokableGraph, checkpoint_store=None):
        self.graph = graph
        self.checkpoint_store = checkpoint_store

    @staticmethod
    def _message_dict(message: Any) -> dict[str, Any] | None:
        if isinstance(message, dict):
            return dict(message)
        role = {
            "ai": "assistant",
            "human": "user",
            "system": "system",
            "tool": "tool",
        }.get(str(getattr(message, "type", "")))
        if role is None:
            return None
        normalized = {"role": role, "content": getattr(message, "content", "")}
        for field_name in ("name", "tool_call_id", "tool_calls"):
            value = getattr(message, field_name, None)
            if value is not None and value != [] and value != "":
                normalized[field_name] = value
        return normalized

    @staticmethod
    def _encode_typed(serializer: Any, value: Any) -> dict[str, str]:
        value_type, payload = serializer.dumps_typed(value)
        return {
            "type": value_type,
            "data": base64.b64encode(payload).decode("ascii"),
        }

    @staticmethod
    def _decode_typed(serializer: Any, value: Any) -> Any:
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("type"), str)
            or not isinstance(value.get("data"), str)
        ):
            raise ValueError("invalid LangGraph checkpoint value")
        payload = base64.b64decode(value["data"], validate=True)
        return serializer.loads_typed((value["type"], payload))

    def _checkpoint_bundle(self, config: dict[str, Any]) -> dict[str, Any] | None:
        checkpointer = getattr(self.graph, "checkpointer", None)
        get_tuple = getattr(checkpointer, "get_tuple", None)
        serializer = getattr(checkpointer, "serde", None)
        if not callable(get_tuple) or serializer is None:
            return None
        checkpoint_tuple = get_tuple(config)
        if checkpoint_tuple is None:
            return None
        pending_writes = [
            {
                "task_id": task_id,
                "channel": channel,
                "value": self._encode_typed(serializer, value),
            }
            for task_id, channel, value in checkpoint_tuple.pending_writes
        ]
        return {
            "version": self._CHECKPOINT_BUNDLE_VERSION,
            "config": self._encode_typed(serializer, checkpoint_tuple.config),
            "checkpoint": self._encode_typed(
                serializer, checkpoint_tuple.checkpoint
            ),
            "metadata": self._encode_typed(serializer, checkpoint_tuple.metadata),
            "parent_config": self._encode_typed(
                serializer, checkpoint_tuple.parent_config
            ),
            "pending_writes": pending_writes,
        }

    def _restore_checkpoint_bundle(self, bundle: dict[str, Any]) -> dict[str, Any]:
        if bundle.get("version") != self._CHECKPOINT_BUNDLE_VERSION:
            raise ValueError("unsupported LangGraph checkpoint version")
        checkpointer = getattr(self.graph, "checkpointer", None)
        serializer = getattr(checkpointer, "serde", None)
        put = getattr(checkpointer, "put", None)
        put_writes = getattr(checkpointer, "put_writes", None)
        if serializer is None or not callable(put) or not callable(put_writes):
            raise ValueError("LangGraph checkpoint cannot be restored")

        saved_config = self._decode_typed(serializer, bundle.get("config"))
        checkpoint = self._decode_typed(serializer, bundle.get("checkpoint"))
        checkpoint_metadata = self._decode_typed(
            serializer, bundle.get("metadata")
        )
        parent_config = self._decode_typed(
            serializer, bundle.get("parent_config")
        )
        if not isinstance(saved_config, dict) or not isinstance(checkpoint, dict):
            raise ValueError("invalid LangGraph checkpoint")
        if parent_config is not None and not isinstance(parent_config, dict):
            raise ValueError("invalid LangGraph parent checkpoint")

        import_config = parent_config
        if import_config is None:
            configurable = saved_config.get("configurable", {})
            if not isinstance(configurable, dict):
                raise ValueError("invalid LangGraph checkpoint config")
            import_config = {
                "configurable": {
                    key: value
                    for key, value in configurable.items()
                    if key != "checkpoint_id"
                }
            }
        restored_config = put(
            import_config,
            checkpoint,
            checkpoint_metadata,
            checkpoint.get("channel_versions", {}),
        )

        pending_writes = bundle.get("pending_writes")
        if not isinstance(pending_writes, list):
            raise ValueError("invalid LangGraph pending writes")
        writes_by_task: dict[str, list[tuple[str, Any]]] = {}
        for item in pending_writes:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("task_id"), str)
                or not isinstance(item.get("channel"), str)
            ):
                raise ValueError("invalid LangGraph pending write")
            writes_by_task.setdefault(item["task_id"], []).append(
                (
                    item["channel"],
                    self._decode_typed(serializer, item.get("value")),
                )
            )
        for task_id, writes in writes_by_task.items():
            put_writes(restored_config, writes, task_id)
        return restored_config

    def _external_state(self, values: dict[str, Any]) -> dict[str, Any]:
        external = {}
        for key, value in values.items():
            if key == self._CHECKPOINT_BUNDLE_KEY:
                continue
            if key == "messages" and isinstance(value, list):
                value = [
                    normalized
                    for message in value
                    if (normalized := self._message_dict(message)) is not None
                ]
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                continue
            external[key] = value
        return external

    def invoke(
        self,
        state: RuntimeState,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeResult:
        initial_state = state.as_dict()
        checkpoint_bundle = None
        if self.checkpoint_store is not None:
            restored = self.checkpoint_store.load_latest(state.run_id)
            if isinstance(restored, dict):
                checkpoint_bundle = restored.get(self._CHECKPOINT_BUNDLE_KEY)
                restored_values = {
                    key: value
                    for key, value in restored.items()
                    if key != self._CHECKPOINT_BUNDLE_KEY
                }
                initial_state = {**initial_state, **restored_values}
        config = {
            "configurable": {"thread_id": state.run_id},
            "metadata": dict(metadata or {}),
        }
        graph_input = initial_state
        if checkpoint_bundle is not None:
            if not isinstance(checkpoint_bundle, dict):
                raise ValueError("invalid LangGraph checkpoint bundle")
            config = {
                **self._restore_checkpoint_bundle(checkpoint_bundle),
                "metadata": dict(metadata or {}),
            }
            graph_input = None
        try:
            output = self.graph.invoke(graph_input, config=config)
        except RunnerApprovalInterruption:
            if self.checkpoint_store is not None:
                interrupted_state = initial_state
                get_state = getattr(self.graph, "get_state", None)
                if callable(get_state):
                    try:
                        checkpoint = get_state(config)
                    except Exception:  # noqa: BLE001
                        checkpoint = None
                    values = getattr(checkpoint, "values", None)
                    if isinstance(values, dict):
                        interrupted_state = self._external_state(values)
                bundle = self._checkpoint_bundle(config)
                if bundle is not None:
                    interrupted_state = {
                        **interrupted_state,
                        self._CHECKPOINT_BUNDLE_KEY: bundle,
                    }
                self.checkpoint_store.save(
                    state.run_id, "interrupted", interrupted_state
                )
            raise
        messages = output.get("messages") if isinstance(output, dict) else None
        if not isinstance(messages, list):
            raise RuntimeError("Graph did not produce an assistant result")
        normalized_messages = [
            normalized
            for message in messages
            if (normalized := self._message_dict(message)) is not None
        ]
        assistant_messages = [
            message for message in normalized_messages
            if message.get("role") == "assistant"
            and isinstance(message.get("content"), str)
            and message["content"].strip()
        ]
        if not assistant_messages:
            raise RuntimeError("Graph did not produce an assistant result")
        normalized_output = {**output, "messages": normalized_messages}
        if self.checkpoint_store is not None:
            self.checkpoint_store.save(state.run_id, "langgraph", normalized_output)
        return RuntimeResult(
            status=str(output.get("status", "completed")),
            content=assistant_messages[-1]["content"],
            state=normalized_output,
        )
