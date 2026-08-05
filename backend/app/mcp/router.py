from fastapi import APIRouter, Depends, HTTPException, Request

from app.audit.management import management_audit_route_class, management_request_id, record_failed_management
from app.core.request_context import RequestContext, require_request_context

from .schemas import McpClientConfig, McpClientCreate, McpClientInfo, McpToolInfo, McpToolWhitelistRequest
from .service import McpConflictError, McpNotFoundError, McpService, McpValidationError


class _LazyMcpService:
    """Defer database-backed service construction until a route is used."""

    def __init__(self) -> None:
        self._service: McpService | None = None

    def _get(self) -> McpService:
        if self._service is None:
            self._service = McpService()
        return self._service

    def __getattr__(self, name: str):
        return getattr(self._get(), name)


def create_router(service: McpService | None = None) -> APIRouter:
    manager = service or _LazyMcpService()
    router = APIRouter(
        dependencies=[Depends(require_request_context)],
        route_class=management_audit_route_class(
            lambda: manager.store.session_factory, lambda: manager.audit_recorder,
            source="mcp", resource_type="mcp_client",
        ),
    )

    def call(operation):
        try:
            return operation()
        except McpNotFoundError as error:
            raise HTTPException(status_code=404, detail=f"MCP client '{error}' was not found") from error
        except McpConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except McpValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    def call_management(operation, session, context: RequestContext, request_id: str | None, action: str, key: str):
        try:
            return operation()
        except McpNotFoundError as error:
            session.rollback()
            record_failed_management(manager.store.session_factory, manager.audit_recorder, context, source="mcp", action=action, resource_type="mcp_client", resource_id=key, error_code="MCP_NOT_FOUND", request_id=request_id)
            raise HTTPException(status_code=404, detail=f"MCP client '{error}' was not found") from error
        except McpConflictError as error:
            session.rollback()
            record_failed_management(manager.store.session_factory, manager.audit_recorder, context, source="mcp", action=action, resource_type="mcp_client", resource_id=key, error_code="MCP_CONFLICT", request_id=request_id)
            raise HTTPException(status_code=409, detail=str(error)) from error
        except McpValidationError as error:
            session.rollback()
            record_failed_management(manager.store.session_factory, manager.audit_recorder, context, source="mcp", action=action, resource_type="mcp_client", resource_id=key, error_code="MCP_VALIDATION", request_id=request_id)
            raise HTTPException(status_code=422, detail=str(error)) from error


    def require_mcp_admin(request: Request, context: RequestContext = Depends(require_request_context)) -> RequestContext:
        if context.role != "admin":
            key = request.path_params.get("client_key", "mcp_clients")
            action = "resource.deleted" if request.method == "DELETE" else "resource.created" if request.method == "POST" and not request.path_params else "resource.updated"
            record_failed_management(manager.store.session_factory, manager.audit_recorder, context, source="mcp", action=action, resource_type="mcp_client", resource_id=key, error_code="PERMISSION_DENIED", request_id=management_request_id(request))
            raise HTTPException(status_code=403, detail="Administrator permission is required")
        request.state.management_context = context
        management_request_id(request)
        return context

    @router.get("", response_model=list[McpClientInfo])
    def list_clients():
        return manager.list()

    @router.post("", response_model=McpClientInfo, status_code=201)
    def create_client(
        request: McpClientCreate,
        context: RequestContext = Depends(require_mcp_admin),
        request_id: str = Depends(management_request_id),
    ):

        with manager.store.session_factory() as session:
            return call_management(lambda: manager.create(request, context=context, session=session, request_id=request_id), session, context, request_id, "resource.created", request.key)

    @router.get("/{client_key}", response_model=McpClientInfo)
    def get_client(client_key: str):
        return call(lambda: manager.get(client_key))

    @router.put("/{client_key}", response_model=McpClientInfo)
    def update_client(client_key: str, request: McpClientConfig, context: RequestContext = Depends(require_mcp_admin), request_id: str = Depends(management_request_id)):
        with manager.store.session_factory() as session:
            return call_management(lambda: manager.update(client_key, request, context=context, session=session, request_id=request_id), session, context, request_id, "resource.updated", client_key)

    @router.patch("/{client_key}/toggle", response_model=McpClientInfo)
    def toggle_client(client_key: str, context: RequestContext = Depends(require_mcp_admin), request_id: str = Depends(management_request_id)):
        with manager.store.session_factory() as session:
            return call_management(lambda: manager.toggle(client_key, context=context, session=session, request_id=request_id), session, context, request_id, "resource.updated", client_key)

    @router.delete("/{client_key}")
    def delete_client(client_key: str, context: RequestContext = Depends(require_mcp_admin), request_id: str = Depends(management_request_id)):
        with manager.store.session_factory() as session:
            call_management(lambda: manager.delete(client_key, context=context, session=session, request_id=request_id), session, context, request_id, "resource.deleted", client_key)
        return {"message": "MCP client deleted"}

    @router.get("/{client_key}/tools", response_model=list[McpToolInfo])
    def list_tools(client_key: str):
        return call(lambda: manager.list_tools(client_key))

    @router.post("/{client_key}/tools/sync", response_model=list[McpToolInfo])
    def sync_tools(client_key: str, context: RequestContext = Depends(require_mcp_admin), request_id: str = Depends(management_request_id)):
        with manager.store.session_factory() as session:
            return call_management(lambda: manager.sync_tools(client_key, context=context, session=session, request_id=request_id), session, context, request_id, "resource.updated", client_key)

    @router.put("/{client_key}/tools", response_model=list[McpToolInfo])
    def update_tools(client_key: str, request: McpToolWhitelistRequest, context: RequestContext = Depends(require_mcp_admin), request_id: str = Depends(management_request_id)):
        with manager.store.session_factory() as session:
            return call_management(lambda: manager.update_whitelist(client_key, request.tools, context=context, session=session, request_id=request_id), session, context, request_id, "resource.permission_changed", client_key)

    return router


router = create_router()
