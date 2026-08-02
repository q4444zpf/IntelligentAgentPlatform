from fastapi import APIRouter, HTTPException

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
    router = APIRouter()
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
    def create_agent(request: AgentCreateRequest):
        return call(lambda: manager.create(request))

    @router.get("/default", response_model=AgentInfo)
    def get_default_agent():
        return call(manager.get_default)

    @router.put("/default", response_model=AgentInfo)
    def set_default_agent(request: AgentDefaultRequest):
        return call(lambda: manager.set_default(request.agent_id))

    @router.get("/{agent_id}", response_model=AgentInfo)
    def get_agent(agent_id: str):
        return call(lambda: manager.get(agent_id))

    @router.put("/{agent_id}", response_model=AgentInfo)
    def update_agent(agent_id: str, request: AgentConfig):
        return call(lambda: manager.update(agent_id, request))

    @router.patch("/{agent_id}/toggle", response_model=AgentInfo)
    def toggle_agent(agent_id: str, request: AgentToggleRequest):
        return call(lambda: manager.set_enabled(agent_id, request.enabled))

    @router.patch("/{agent_id}/pin", response_model=AgentInfo)
    def pin_agent(agent_id: str, request: AgentPinRequest):
        return call(lambda: manager.set_pinned(agent_id, request.pinned))

    @router.post("/{agent_id}/copy", response_model=AgentInfo, status_code=201)
    def copy_agent(agent_id: str, request: AgentCopyRequest):
        return call(lambda: manager.copy(agent_id, request))

    @router.delete("/{agent_id}")
    def delete_agent(agent_id: str):
        call(lambda: manager.delete(agent_id))
        return {"success": True, "agent_id": agent_id}

    return router


router = create_router()
