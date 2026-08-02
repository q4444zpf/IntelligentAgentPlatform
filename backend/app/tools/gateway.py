from __future__ import annotations

import json
import re
import time
from collections.abc import Collection, Callable
from datetime import datetime, timezone
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from sqlalchemy.exc import IntegrityError

from app.conversations.models import ToolInvocation
from app.conversations.repository import ConversationRepository

from .builtins import BUILTIN_EXECUTORS
from .schemas import ToolCall, ToolExecutionContext, ToolExecutionResult, ToolRuntimeError
from .store import ToolStore

_SENSITIVE_KEY = re.compile(r"authorization|api_?key|token|secret|password|credential", re.IGNORECASE)
_REDACTED = "[REDACTED]"
_TRUNCATED = "[TRUNCATED]"


class ToolGateway:
    def __init__(
        self,
        *,
        tool_store: ToolStore,
        repository: ConversationRepository,
        clock: Callable[[], datetime] | None = None,
    ):
        self.tool_store = tool_store
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _validate(schema: dict[str, Any], value: Any, code: str, message: str) -> None:
        try:
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(value)
        except (SchemaError, ValidationError) as error:
            raise ToolRuntimeError(code, message) from error

    @classmethod
    def _summarize_value(cls, value: Any, depth: int = 0) -> Any:
        if depth >= 5 and isinstance(value, (dict, list, tuple)):
            return _TRUNCATED
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, item in list(value.items())[:50]:
                key_text = str(key)
                result[key_text] = (
                    _REDACTED
                    if _SENSITIVE_KEY.search(key_text)
                    else cls._summarize_value(item, depth + 1)
                )
            return result
        if isinstance(value, (list, tuple)):
            return [cls._summarize_value(item, depth + 1) for item in value[:20]]
        if isinstance(value, str):
            return value if len(value) <= 512 else value[:500] + _TRUNCATED
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return str(value)[:500]

    @classmethod
    def summarize(cls, value: Any) -> Any:
        summary = cls._summarize_value(value)
        if len(cls.serialize_summary(summary).encode("utf-8")) <= 4096:
            return summary
        if isinstance(summary, dict):
            bounded: dict[str, Any] = {}
            for key, item in summary.items():
                candidate = {**bounded, key: item}
                if len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 4050:
                    break
                bounded[key] = item
            bounded["_summary"] = _TRUNCATED
            return bounded
        return {"_summary": _TRUNCATED}

    @staticmethod
    def serialize_summary(summary: Any) -> str:
        serialized = json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
        if len(serialized.encode("utf-8")) <= 4096:
            return serialized
        return json.dumps({"_summary": _TRUNCATED}, separators=(",", ":"))

    def _commit_started(self, invocation: ToolInvocation, display_name: str) -> None:
        self.repository.add_tool_invocation(invocation)
        self.repository.append_event(
            invocation.run_id,
            "tool.started",
            {
                "invocation_id": invocation.id,
                "tool_id": invocation.tool_id,
                "display_name": display_name,
            },
        )
        self.repository.session.commit()

    def _commit_finished(
        self,
        invocation: ToolInvocation,
        display_name: str,
        *,
        status: str,
        duration_ms: int,
        result: dict[str, Any] | None = None,
        error: ToolRuntimeError | None = None,
    ) -> None:
        invocation.status = status
        invocation.duration_ms = duration_ms
        invocation.completed_at = datetime.now(timezone.utc)
        if result is not None:
            invocation.result_summary = self.summarize(result)
        payload: dict[str, Any] = {
            "invocation_id": invocation.id,
            "tool_id": invocation.tool_id,
            "display_name": display_name,
            "duration_ms": duration_ms,
        }
        event_type = "tool.completed"
        invocation.error_code = error.code if error is not None else None
        if error is not None:
            event_type = "tool.failed"
            payload.update(code=error.code, message=error.safe_message)
        self.repository.append_event(invocation.run_id, event_type, payload)
        self.repository.session.commit()

    def execute(
        self,
        call: ToolCall,
        context: ToolExecutionContext,
        authorized_tool_ids: Collection[str],
    ) -> ToolExecutionResult:
        if call.name not in authorized_tool_ids:
            raise ToolRuntimeError("tool_not_authorized", "该工具当前不可用。")
        tool = self.tool_store.get_executable(call.name)
        executor = BUILTIN_EXECUTORS.get(call.name)
        if tool is None or executor is None or tool["source"] != "builtin":
            raise ToolRuntimeError("tool_not_authorized", "该工具当前不可用。")
        if self.repository.get_tool_invocation(context.run_id, call.id) is not None:
            raise ToolRuntimeError("tool_duplicate_call", "工具调用标识重复。")

        self._validate(tool["input_schema"], call.arguments, "tool_invalid_arguments", "工具参数无效。")
        invocation = ToolInvocation(
            run_id=context.run_id,
            tool_call_id=call.id,
            tool_id=call.name,
            tool_version=tool["version"],
            status="started",
            arguments_summary=self.summarize(call.arguments),
        )
        try:
            self._commit_started(invocation, tool["name"])
        except IntegrityError as error:
            self.repository.session.rollback()
            raise ToolRuntimeError("tool_duplicate_call", "工具调用标识重复。") from error

        started_at = time.perf_counter()
        try:
            value = executor(call.arguments, context, self.clock)
            self._validate(tool["output_schema"], value, "tool_execution_failed", "工具执行失败。")
        except ToolRuntimeError as error:
            duration_ms = max(0, round((time.perf_counter() - started_at) * 1000))
            self._commit_finished(invocation, tool["name"], status="failed", duration_ms=duration_ms, error=error)
            raise
        except Exception as error:
            safe_error = ToolRuntimeError("tool_execution_failed", "工具执行失败。")
            duration_ms = max(0, round((time.perf_counter() - started_at) * 1000))
            self._commit_finished(invocation, tool["name"], status="failed", duration_ms=duration_ms, error=safe_error)
            raise safe_error from error

        duration_ms = max(0, round((time.perf_counter() - started_at) * 1000))
        self._commit_finished(invocation, tool["name"], status="completed", duration_ms=duration_ms, result=value)
        return ToolExecutionResult(invocation_id=invocation.id, value=value)