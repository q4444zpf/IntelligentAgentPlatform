from fastapi import APIRouter, Depends, Header, HTTPException
from datetime import UTC, datetime
from uuid import uuid4

from app.core.request_context import RequestContext, require_admin_context, require_request_context
from app.audit.recorder import AuditRecordRequest

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


def create_router(service: AgentService | None = None) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_request_context)])
    manager = service or AgentService()

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

    @router.get("", response_model=list[AgentInfo])
    def list_agents():
        return manager.list()

    @router.post("", response_model=AgentInfo, status_code=201)
    def create_agent(
        request: AgentCreateRequest,
        context: RequestContext = Depends(require_admin_context),
        request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        with manager.store.session_factory() as session:
            return call(lambda: manager.create(request, context=context, session=session, request_id=request_id))

    @router.get("/default", response_model=AgentInfo)
    def get_default_agent():
        return call(manager.get_default)

    @router.put("/default", response_model=AgentInfo)
    def set_default_agent(request: AgentDefaultRequest, context: RequestContext = Depends(require_admin_context), request_id: str | None = Header(default=None, alias="X-Request-ID")):
        with manager.store.session_factory() as session:
            return call(lambda: manager.set_default(request.agent_id, context=context, session=session, request_id=request_id))

    @router.get("/{agent_id}", response_model=AgentInfo)
    def get_agent(agent_id: str):
        return call(lambda: manager.get(agent_id))

    @router.put("/{agent_id}", response_model=AgentInfo)
    def update_agent(agent_id: str, request: AgentConfig, context: RequestContext = Depends(require_admin_context), request_id: str | None = Header(default=None, alias="X-Request-ID")):
        with manager.store.session_factory() as session:
            return call(lambda: manager.update(agent_id, request, context=context, session=session, request_id=request_id))

    @router.patch("/{agent_id}/toggle", response_model=AgentInfo)
    def toggle_agent(agent_id: str, request: AgentToggleRequest, context: RequestContext = Depends(require_admin_context), request_id: str | None = Header(default=None, alias="X-Request-ID")):
        with manager.store.session_factory() as session:
            return call(lambda: manager.set_enabled(agent_id, request.enabled, context=context, session=session, request_id=request_id))

    @router.patch("/{agent_id}/pin", response_model=AgentInfo)
    def pin_agent(agent_id: str, request: AgentPinRequest, context: RequestContext = Depends(require_admin_context), request_id: str | None = Header(default=None, alias="X-Request-ID")):
        with manager.store.session_factory() as session:
            return call(lambda: manager.set_pinned(agent_id, request.pinned, context=context, session=session, request_id=request_id))

    @router.post("/{agent_id}/copy", response_model=AgentInfo, status_code=201)
    def copy_agent(agent_id: str, request: AgentCopyRequest, context: RequestContext = Depends(require_admin_context), request_id: str | None = Header(default=None, alias="X-Request-ID")):
        with manager.store.session_factory() as session:
            return call(lambda: manager.copy(agent_id, request, context=context, session=session, request_id=request_id))

    @router.delete("/{agent_id}")
    def delete_agent(
        agent_id: str,
        context: RequestContext = Depends(require_admin_context),
        request_id: str | None = Header(default=None, alias="X-Request-ID"),
    ):
        try:
            with manager.store.session_factory() as session:
                manager.delete(agent_id, context=context, session=session, request_id=request_id)
        except AgentNotFoundError as error:
            _record_failed_delete(context, request_id, agent_id, "AGENT_NOT_FOUND")
            raise HTTPException(status_code=404, detail=f"Agent '{error}' was not found") from error
        except AgentProtectedError as error:
            _record_failed_delete(context, request_id, agent_id, "AGENT_PROTECTED")
            raise HTTPException(status_code=409, detail=str(error)) from error
        except AgentConcurrentUpdateError as error:
            _record_failed_delete(context, request_id, agent_id, "AGENT_STALE_UPDATE")
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"success": True, "agent_id": agent_id}

    def _record_failed_delete(
        context: RequestContext,
        request_id: str | None,
        agent_id: str,
        error_code: str,
    ) -> None:
        stable_request_id = request_id or str(uuid4())
        with manager.store.session_factory.begin() as audit_session:
            manager.audit_recorder.record(audit_session, AuditRecordRequest(
                unit_id=context.unit_id, project_id=context.project_id,
                user_id=context.user_id, actor_role=context.role,
                category="management", source="agent", action="resource.deleted",
                status="failed", risk_level="high", resource_type="agent",
                resource_id=agent_id, summary=f"Agent {agent_id} deletion failed",
                metadata={}, allowed_metadata_keys=frozenset(), error_code=error_code,
                idempotency_key=f"management:{stable_request_id}:agent.delete:{agent_id}",
                occurred_at=datetime.now(UTC),
            ))

    return router


router = create_router()
