from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import uuid4
from sqlalchemy.orm import Session

from app.audit.management import management_event_id, management_trace_id
from app.audit.recorder import AuditRecorder, AuditRecordRequest
from app.core.request_context import RequestContext
from .builtins import BUILTIN_TOOL_DEFINITIONS
from .schemas import ToolInfo
from .store import ToolStore

TOOL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


class ToolNotFoundError(Exception):
    pass


class ToolValidationError(Exception):
    pass


class ToolService:
    def __init__(self, store: ToolStore | None = None, *, audit_recorder: AuditRecorder | None = None):
        self.store = store or ToolStore()
        self.audit_recorder = audit_recorder or AuditRecorder()
        self._ensure_builtins()

    def _ensure_builtins(self) -> None:
        for definition in BUILTIN_TOOL_DEFINITIONS:
            self.store.upsert_builtin(definition)

    @staticmethod
    def _validate_tool_id(tool_id: str) -> None:
        if not TOOL_ID_PATTERN.fullmatch(tool_id):
            raise ToolValidationError("Invalid tool ID")

    def list(self) -> list[ToolInfo]:
        return [ToolInfo.model_validate(item) for item in self.store.list()]

    def get(self, tool_id: str) -> ToolInfo:
        self._validate_tool_id(tool_id)
        item = self.store.get(tool_id)
        if item is None:
            raise ToolNotFoundError(tool_id)
        return ToolInfo.model_validate(item)

    def resolve_bindable(self, tool_ids: list[str]) -> list[ToolInfo]:
        resolved = []
        for tool_id in tool_ids:
            tool = self.get(tool_id)
            if not tool.published or not tool.enabled:
                raise ToolValidationError(
                    f"Tool '{tool_id}' is not available for binding"
                )
            resolved.append(tool)
        return resolved

    def toggle(
        self,
        tool_id: str,
        *,
        context: RequestContext | None = None,
        session: Session | None = None,
        request_id: str | None = None,
    ) -> ToolInfo:
        self._validate_tool_id(tool_id)
        if context is None or session is None:
            updated = self.store.toggle(tool_id)
            if updated is None:
                raise ToolNotFoundError(tool_id)
            return ToolInfo.model_validate(updated)
        try:
            updated = self.store.toggle_in_session(session, tool_id)
            if updated is None:
                raise ToolNotFoundError(tool_id)
            enabled = bool(updated["enabled"])
            self.audit_recorder.record(session, AuditRecordRequest(
                unit_id=context.unit_id, project_id=context.project_id,
                user_id=context.user_id, actor_roles=context.role_codes,
                authorization_scope="project", event_scope="project",
                trace_id=management_trace_id(request_id), category="management", source="tool",
                action="resource.enabled" if enabled else "resource.disabled",
                status="succeeded", risk_level="medium",
                resource_type="tool", resource_id=tool_id,
                summary=f"Tool {tool_id} was {'enabled' if enabled else 'disabled'}",
                metadata={"enabled": enabled}, allowed_metadata_keys=frozenset({"enabled"}),
                idempotency_key=f"management:{management_event_id(request_id)}:succeeded:tool.toggle:{tool_id}",
                occurred_at=datetime.now(UTC),
            ))
            session.commit()
        except Exception:
            session.rollback()
            raise
        return ToolInfo.model_validate(updated)

    def delete(self, tool_id: str) -> None:
        tool = self.get(tool_id)
        if tool.is_builtin:
            raise ToolValidationError("Built-in tools cannot be deleted")
        raise ToolValidationError("Tool deletion is not supported")
