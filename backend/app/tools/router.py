from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException

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

    @router.get("", response_model=list[ToolInfo])
    def list_tools():
        return manager().list()

    @router.get("/{tool_id}", response_model=ToolInfo)
    def get_tool(tool_id: str):
        return call(lambda: manager().get(tool_id))

    @router.patch("/{tool_id}/toggle", response_model=ToolInfo)
    def toggle_tool(tool_id: str):
        return call(lambda: manager().toggle(tool_id))

    return router


router = create_router()
