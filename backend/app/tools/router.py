from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.audit.recorder import AuditRecordRequest
from app.core.request_context import (
    RequestContext,
    require_admin_context,
    require_request_context,
)

from .schemas import ToolInfo
from .service import ToolNotFoundError, ToolService, ToolValidationError


def create_router(service: ToolService | None = None) -> APIRouter:
    router = APIRouter()
    def manager() -> ToolService:
        return service or ToolService()

    def call(operation: Callable[[], Any]):
        try:
            return operation()
        except ToolNotFoundError as error:
            raise HTTPException(status_code=404, detail=f"Tool '{error}' was not found") from error
        except ToolValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    def require_tool_admin(
        request: Request,
        context: RequestContext = Depends(require_request_context),
    ) -> RequestContext:
        if context.role == "admin":
            return context
        service_instance = manager()
        tool_id = request.path_params.get("tool_id", "unknown")
        current = service_instance.store.get(tool_id)
        action = "resource.disabled" if current and current["enabled"] else "resource.enabled"
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        with service_instance.store.session_factory.begin() as session:
            service_instance.audit_recorder.record(session, AuditRecordRequest(
                unit_id=context.unit_id, project_id=context.project_id,
                user_id=context.user_id, actor_role=context.role,
                category="management", source="tool", action=action,
                status="failed", risk_level="medium", resource_type="tool",
                resource_id=tool_id, summary=f"Tool {tool_id} toggle permission denied",
                metadata={}, allowed_metadata_keys=frozenset(),
                error_code="PERMISSION_DENIED",
                idempotency_key=f"management:{request_id}:tool.toggle:{tool_id}",
                occurred_at=datetime.now(UTC),
            ))
        raise HTTPException(
            status_code=403,
            detail="Administrator permission is required",
        )


    @router.get("", response_model=list[ToolInfo])
    def list_tools(
        _context: RequestContext = Depends(require_request_context),
    ):
        return manager().list()

    @router.get("/{tool_id}", response_model=ToolInfo)
    def get_tool(
        tool_id: str,
        _context: RequestContext = Depends(require_request_context),
    ):
        return call(lambda: manager().get(tool_id))

    @router.patch("/{tool_id}/toggle", response_model=ToolInfo)
    def toggle_tool(
        tool_id: str,
        context: RequestContext = Depends(require_tool_admin),
        request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        service_instance = manager()
        with service_instance.store.session_factory() as session:
            return call(lambda: service_instance.toggle(tool_id, context=context, session=session, request_id=request_id))

    return router


router = create_router()
