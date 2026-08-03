from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.audit.management import management_audit_route_class, record_failed_management
from app.core.request_context import (
    RequestContext,
    require_request_context,
)

from .schemas import ToolInfo
from .service import ToolNotFoundError, ToolService, ToolValidationError


def create_router(service: ToolService | None = None) -> APIRouter:
    router = APIRouter(
        route_class=management_audit_route_class(
            lambda: manager().store.session_factory, lambda: manager().audit_recorder,
            source="tool", resource_type="tool",
        )
    )
    def manager() -> ToolService:
        return service or ToolService()

    def call(operation: Callable[[], Any]):
        try:
            return operation()
        except ToolNotFoundError as error:
            raise HTTPException(status_code=404, detail=f"Tool '{error}' was not found") from error
        except ToolValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    def call_management(operation, session, context: RequestContext, request_id: str | None, tool_id: str):
        try:
            return operation()
        except ToolNotFoundError as error:
            session.rollback()
            record_failed_management(manager().store.session_factory, manager().audit_recorder, context, source="tool", action="resource.updated", resource_type="tool", resource_id=tool_id, error_code="TOOL_NOT_FOUND", request_id=request_id, risk_level="medium")
            raise HTTPException(status_code=404, detail=f"Tool '{error}' was not found") from error
        except ToolValidationError as error:
            session.rollback()
            record_failed_management(manager().store.session_factory, manager().audit_recorder, context, source="tool", action="resource.updated", resource_type="tool", resource_id=tool_id, error_code="TOOL_VALIDATION", request_id=request_id, risk_level="medium")
            raise HTTPException(status_code=422, detail=str(error)) from error

    def require_tool_admin(
        request: Request,
        context: RequestContext = Depends(require_request_context),
    ) -> RequestContext:
        if context.role == "admin":
            request.state.management_context = context
            return context
        service_instance = manager()
        tool_id = request.path_params.get("tool_id", "unknown")
        current = service_instance.store.get(tool_id)
        action = "resource.disabled" if current and current["enabled"] else "resource.enabled"
        request_id = request.headers.get("X-Request-ID")
        record_failed_management(service_instance.store.session_factory, service_instance.audit_recorder, context, source="tool", action=action, resource_type="tool", resource_id=tool_id, error_code="PERMISSION_DENIED", request_id=request_id, risk_level="medium")
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
            return call_management(lambda: service_instance.toggle(tool_id, context=context, session=session, request_id=request_id), session, context, request_id, tool_id)

    return router


router = create_router()
