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

from app.audit.models import AuditEvent
from app.audit.recorder import AuditRecorder, AuditRecordRequest
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
        audit_recorder: AuditRecorder | None = None,
    ):
        self.tool_store = tool_store
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.audit_recorder = audit_recorder or AuditRecorder()

    @staticmethod
    def _validate(schema: dict[str, Any], value: Any, code: str, message: str) -> None:
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as error:
            raise ToolRuntimeError("tool_execution_failed", "工具执行失败。") from error
        try:
            Draft202012Validator(schema).validate(value)
        except ValidationError as error:
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

    def _commit_started(
        self, invocation: ToolInvocation, display_name: str, context: ToolExecutionContext,
    ) -> AuditEvent:
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
        audit_event = self.audit_recorder.record(
            self.repository.session,
            AuditRecordRequest(
                unit_id=context.unit_id, project_id=context.project_id,
                user_id=context.user_id, category="runtime", source="tool",
                action="tool.invoke.started", status="started", risk_level="low",
                trace_id=context.run_id, run_id=context.run_id,
                resource_type="tool", resource_id=invocation.tool_id,
                resource_name=display_name,
                idempotency_key=f"tool:{invocation.id}:started",
                occurred_at=datetime.now(timezone.utc),
            ),
        )
        self.repository.session.commit()
        return audit_event

    @staticmethod
    def _apply_failed_state(
        invocation: ToolInvocation,
        error: ToolRuntimeError,
        duration_ms: int,
    ) -> None:
        invocation.status = "failed"
        invocation.error_code = error.code
        invocation.duration_ms = duration_ms
        invocation.completed_at = datetime.now(timezone.utc)

    def _rollback_safely(self) -> None:
        try:
            self.repository.session.rollback()
        except Exception:
            pass

    def _persist_terminal(
        self,
        invocation: ToolInvocation,
        display_name: str,
        *,
        status: str,
        duration_ms: int,
        context: ToolExecutionContext,
        parent_event_id: str,
        result: dict[str, Any] | None,
        error: ToolRuntimeError | None,
        include_audit: bool = True,
    ) -> None:
        invocation.status = status
        invocation.duration_ms = duration_ms
        invocation.completed_at = datetime.now(timezone.utc)
        invocation.error_code = error.code if error is not None else None
        if result is not None:
            invocation.result_summary = self.summarize(result)
        payload: dict[str, Any] = {
            "invocation_id": invocation.id,
            "tool_id": invocation.tool_id,
            "display_name": display_name,
            "duration_ms": duration_ms,
        }
        event_type = "tool.completed"
        if error is not None:
            event_type = "tool.failed"
            payload.update(code=error.code, message=error.safe_message)
        self.repository.append_event(invocation.run_id, event_type, payload)
        if include_audit:
            audit_status = "failed" if error is not None else "succeeded"
            self.audit_recorder.record(
                self.repository.session,
                AuditRecordRequest(
                    unit_id=context.unit_id,
                    project_id=context.project_id,
                    user_id=context.user_id,
                    category="runtime",
                    source="tool",
                    action=f"tool.invoke.{audit_status}",
                    status=audit_status,
                    risk_level="medium" if error is not None else "low",
                    trace_id=context.run_id,
                    run_id=context.run_id,
                    parent_event_id=parent_event_id,
                    resource_type="tool",
                    resource_id=invocation.tool_id,
                    resource_name=display_name,
                    idempotency_key=f"tool:{invocation.id}:{audit_status}",
                    occurred_at=datetime.now(timezone.utc),
                    duration_ms=duration_ms,
                    error_code=error.code if error is not None else None,
                ),
            )
        self.repository.session.commit()

    def _compensate_failed_completion(
        self,
        invocation_id: str,
        display_name: str,
        duration_ms: int,
        error: ToolRuntimeError | None,
        *,
        status: str,
        result: dict[str, Any] | None,
        context: ToolExecutionContext,
        parent_event_id: str,
    ) -> bool:
        try:
            invocation = self.repository.session.get(ToolInvocation, invocation_id)
            if invocation is None:
                return False
            self._persist_terminal(
                invocation, display_name, status=status, duration_ms=duration_ms,
                context=context, parent_event_id=parent_event_id,
                result=result, error=error,
            )
            return True
        except Exception:
            self._rollback_safely()

        try:
            invocation = self.repository.session.get(ToolInvocation, invocation_id)
            if invocation is None:
                return False
            self._persist_terminal(
                invocation, display_name, status=status, duration_ms=duration_ms,
                context=context, parent_event_id=parent_event_id,
                result=result, error=error, include_audit=False,
            )
        except Exception:
            self._rollback_safely()
        return False

    def _commit_finished(
        self,
        invocation: ToolInvocation,
        display_name: str,
        *,
        status: str,
        duration_ms: int,
        context: ToolExecutionContext,
        parent_event_id: str,
        result: dict[str, Any] | None = None,
        error: ToolRuntimeError | None = None,
    ) -> None:
        invocation_id = str(invocation.id)
        try:
            self._persist_terminal(
                invocation, display_name, status=status, duration_ms=duration_ms,
                context=context, parent_event_id=parent_event_id,
                result=result, error=error,
            )
            return
        except Exception as database_error:
            self._rollback_safely()
            if self._compensate_failed_completion(
                invocation_id, display_name, duration_ms, error,
                status=status, result=result, context=context,
                parent_event_id=parent_event_id,
            ):
                return
            raise ToolRuntimeError(
                "tool_execution_failed", "工具执行失败。"
            ) from database_error
    def execute(
        self,
        call: ToolCall,
        context: ToolExecutionContext,
        authorized_tool_ids: Collection[str],
    ) -> ToolExecutionResult:
        if call.name not in authorized_tool_ids:
            raise ToolRuntimeError("tool_not_authorized", "该工具当前不可用。")
        tool = self.tool_store.get_executable(call.name)
        if tool is None:
            raise ToolRuntimeError("tool_not_authorized", "该工具当前不可用。")
        executor = BUILTIN_EXECUTORS.get(call.name)
        if executor is None or tool["source"] != "builtin":
            raise ToolRuntimeError("tool_execution_failed", "工具执行失败。")
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
            started_audit = self._commit_started(invocation, tool["name"], context)
        except Exception as database_error:
            self._rollback_safely()
            if isinstance(database_error, IntegrityError):
                try:
                    duplicate = self.repository.get_tool_invocation(
                        context.run_id, call.id
                    )
                except Exception:
                    self._rollback_safely()
                    duplicate = None
                if duplicate is not None:
                    raise ToolRuntimeError(
                        "tool_duplicate_call", "工具调用标识重复。"
                    ) from database_error
            raise ToolRuntimeError(
                "tool_execution_failed", "工具执行失败。"
            ) from database_error

        invocation_id = str(invocation.id)
        started_at = time.perf_counter()
        try:
            value = executor(call.arguments, context, self.clock)
            self._validate(tool["output_schema"], value, "tool_execution_failed", "工具执行失败。")
        except ToolRuntimeError as error:
            duration_ms = max(0, round((time.perf_counter() - started_at) * 1000))
            self._commit_finished(
                invocation, tool["name"], status="failed", duration_ms=duration_ms,
                error=error, context=context, parent_event_id=started_audit.id,
            )
            raise
        except Exception as error:
            safe_error = ToolRuntimeError("tool_execution_failed", "工具执行失败。")
            duration_ms = max(0, round((time.perf_counter() - started_at) * 1000))
            self._commit_finished(
                invocation, tool["name"], status="failed", duration_ms=duration_ms,
                error=safe_error, context=context, parent_event_id=started_audit.id,
            )
            raise safe_error from error

        duration_ms = max(0, round((time.perf_counter() - started_at) * 1000))
        self._commit_finished(
            invocation, tool["name"], status="completed", duration_ms=duration_ms,
            result=value, context=context, parent_event_id=started_audit.id,
        )
        return ToolExecutionResult(invocation_id=invocation_id, value=value)
