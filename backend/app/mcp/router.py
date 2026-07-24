from fastapi import APIRouter, HTTPException

from .schemas import McpClientConfig, McpClientCreate, McpClientInfo, McpToolInfo, McpToolWhitelistRequest
from .service import McpConflictError, McpNotFoundError, McpService, McpValidationError


def create_router(service: McpService | None = None) -> APIRouter:
    router = APIRouter()
    manager = service or McpService()

    def call(operation):
        try:
            return operation()
        except McpNotFoundError as error:
            raise HTTPException(status_code=404, detail=f"MCP client '{error}' was not found") from error
        except McpConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except McpValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("", response_model=list[McpClientInfo])
    def list_clients():
        return manager.list()

    @router.post("", response_model=McpClientInfo, status_code=201)
    def create_client(request: McpClientCreate):
        return call(lambda: manager.create(request))

    @router.get("/{client_key}", response_model=McpClientInfo)
    def get_client(client_key: str):
        return call(lambda: manager.get(client_key))

    @router.put("/{client_key}", response_model=McpClientInfo)
    def update_client(client_key: str, request: McpClientConfig):
        return call(lambda: manager.update(client_key, request))

    @router.patch("/{client_key}/toggle", response_model=McpClientInfo)
    def toggle_client(client_key: str):
        return call(lambda: manager.toggle(client_key))

    @router.delete("/{client_key}")
    def delete_client(client_key: str):
        call(lambda: manager.delete(client_key))
        return {"message": "MCP client deleted"}

    @router.get("/{client_key}/tools", response_model=list[McpToolInfo])
    def list_tools(client_key: str):
        return call(lambda: manager.list_tools(client_key))

    @router.post("/{client_key}/tools/sync", response_model=list[McpToolInfo])
    def sync_tools(client_key: str):
        return call(lambda: manager.sync_tools(client_key))

    @router.put("/{client_key}/tools", response_model=list[McpToolInfo])
    def update_tools(client_key: str, request: McpToolWhitelistRequest):
        return call(lambda: manager.update_whitelist(client_key, request.tools))

    return router


router = create_router()
