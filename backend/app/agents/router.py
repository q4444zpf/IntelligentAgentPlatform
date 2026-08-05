from fastapi import APIRouter, Depends, HTTPException, Request
from app.core.request_context import RequestContext, require_request_context
from app.audit.management import management_audit_route_class, management_request_id, record_failed_management

from .schemas import (
    AgentConfig,
    AgentCopyRequest,
    AgentCreateRequest,
    AgentDefaultRequest,
    AgentInfo,
    AgentPinRequest,
    AgentToggleRequest,
)
from .service import (
    AgentConflictError,
    AgentNotFoundError,
    AgentProtectedError,
    AgentService,
    AgentValidationError,
)
from .store import AgentConcurrentUpdateError


class _LazyAgentService:
    """Defer database-backed service construction until a route is used."""

    def __init__(self) -> None:
        self._service: AgentService | None = None

    def _get(self) -> AgentService:
        if self._service is None:
            self._service = AgentService()
        return self._service

    def __getattr__(self, name: str):
        return getattr(self._get(), name)


def create_router(service: AgentService | None = None) -> APIRouter:
    manager = service or _LazyAgentService()
    router = APIRouter(
        dependencies=[Depends(require_request_context)],
        route_class=management_audit_route_class(
            lambda: manager.store.session_factory, lambda: manager.audit_recorder,
            source="agent", resource_type="agent",
        ),
    )

    def call(operation):
        try:
            return operation()
        except AgentNotFoundError as error:
            raise HTTPException(status_code=404, detail=f"Agent '{error}' was not found") from error
        except AgentConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (AgentProtectedError, AgentConcurrentUpdateError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except AgentValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    def call_management(operation, session, context: RequestContext, request_id: str | None, action: str, resource_id: str):
        try:
            return operation()
        except AgentNotFoundError as error:
            session.rollback()
            record_failed_management(manager.store.session_factory, manager.audit_recorder, context, source="agent", action=action, resource_type="agent", resource_id=resource_id, error_code="AGENT_NOT_FOUND", request_id=request_id)
            raise HTTPException(status_code=404, detail=f"Agent '{error}' was not found") from error
        except AgentConflictError as error:
            session.rollback()
            record_failed_management(manager.store.session_factory, manager.audit_recorder, context, source="agent", action=action, resource_type="agent", resource_id=resource_id, error_code="AGENT_CONFLICT", request_id=request_id)
            raise HTTPException(status_code=409, detail=str(error)) from error
        except AgentProtectedError as error:
            session.rollback()
            record_failed_management(manager.store.session_factory, manager.audit_recorder, context, source="agent", action=action, resource_type="agent", resource_id=resource_id, error_code="AGENT_PROTECTED", request_id=request_id)
            raise HTTPException(status_code=409, detail=str(error)) from error
        except AgentConcurrentUpdateError as error:
            session.rollback()
            record_failed_management(manager.store.session_factory, manager.audit_recorder, context, source="agent", action=action, resource_type="agent", resource_id=resource_id, error_code="AGENT_STALE_UPDATE", request_id=request_id)
            raise HTTPException(status_code=409, detail=str(error)) from error
        except AgentValidationError as error:
            session.rollback()
            record_failed_management(manager.store.session_factory, manager.audit_recorder, context, source="agent", action=action, resource_type="agent", resource_id=resource_id, error_code="AGENT_VALIDATION", request_id=request_id)
            raise HTTPException(status_code=422, detail=str(error)) from error

    def require_agent_admin(request: Request, context: RequestContext = Depends(require_request_context)) -> RequestContext:
        if context.role == "admin":
            request.state.management_context = context
            management_request_id(request)
            return context
        action = "resource.deleted" if request.method == "DELETE" else "resource.created" if request.method == "POST" else "resource.updated"
        resource_id = request.path_params.get("agent_id", "agents")
        record_failed_management(manager.store.session_factory, manager.audit_recorder, context, source="agent", action=action, resource_type="agent", resource_id=resource_id, error_code="PERMISSION_DENIED", request_id=management_request_id(request))
        raise HTTPException(status_code=403, detail="Administrator permission is required")


    @router.get("", response_model=list[AgentInfo])
    def list_agents():
        return manager.list()

    @router.post("", response_model=AgentInfo, status_code=201)
    def create_agent(
        request: AgentCreateRequest,
        context: RequestContext = Depends(require_agent_admin),
        request_id: str = Depends(management_request_id),
    ):
        with manager.store.session_factory() as session:
            return call_management(lambda: manager.create(request, context=context, session=session, request_id=request_id), session, context, request_id, "resource.created", request.id)

    @router.get("/default", response_model=AgentInfo)
    def get_default_agent():
        return call(manager.get_default)

    @router.put("/default", response_model=AgentInfo)
    def set_default_agent(request: AgentDefaultRequest, context: RequestContext = Depends(require_agent_admin), request_id: str = Depends(management_request_id)):
        with manager.store.session_factory() as session:
            return call_management(lambda: manager.set_default(request.agent_id, context=context, session=session, request_id=request_id), session, context, request_id, "resource.updated", request.agent_id)

    @router.get("/{agent_id}", response_model=AgentInfo)
    def get_agent(agent_id: str):
        return call(lambda: manager.get(agent_id))

    @router.put("/{agent_id}", response_model=AgentInfo)
    def update_agent(agent_id: str, request: AgentConfig, context: RequestContext = Depends(require_agent_admin), request_id: str = Depends(management_request_id)):
        with manager.store.session_factory() as session:
            return call_management(lambda: manager.update(agent_id, request, context=context, session=session, request_id=request_id), session, context, request_id, "resource.updated", agent_id)

    @router.patch("/{agent_id}/toggle", response_model=AgentInfo)
    def toggle_agent(agent_id: str, request: AgentToggleRequest, context: RequestContext = Depends(require_agent_admin), request_id: str = Depends(management_request_id)):
        with manager.store.session_factory() as session:
            return call_management(lambda: manager.set_enabled(agent_id, request.enabled, context=context, session=session, request_id=request_id), session, context, request_id, "resource.enabled" if request.enabled else "resource.disabled", agent_id)

    @router.patch("/{agent_id}/pin", response_model=AgentInfo)
    def pin_agent(agent_id: str, request: AgentPinRequest, context: RequestContext = Depends(require_agent_admin), request_id: str = Depends(management_request_id)):
        with manager.store.session_factory() as session:
            return call_management(lambda: manager.set_pinned(agent_id, request.pinned, context=context, session=session, request_id=request_id), session, context, request_id, "resource.updated", agent_id)

    @router.post("/{agent_id}/copy", response_model=AgentInfo, status_code=201)
    def copy_agent(agent_id: str, request: AgentCopyRequest, context: RequestContext = Depends(require_agent_admin), request_id: str = Depends(management_request_id)):
        with manager.store.session_factory() as session:
            return call_management(lambda: manager.copy(agent_id, request, context=context, session=session, request_id=request_id), session, context, request_id, "resource.created", request.id)

    @router.delete("/{agent_id}")
    def delete_agent(
        agent_id: str,
        context: RequestContext = Depends(require_agent_admin),
        request_id: str = Depends(management_request_id),
    ):
        with manager.store.session_factory() as session:
            call_management(lambda: manager.delete(agent_id, context=context, session=session, request_id=request_id), session, context, request_id, "resource.deleted", agent_id)
        return {"success": True, "agent_id": agent_id}

    return router


router = create_router()
