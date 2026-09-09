from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse, Response

PROJECT_SKILL_ROOT = "/api/project-skills"


def _is_project_skill_path(path: str) -> bool:
    return path == PROJECT_SKILL_ROOT or path.startswith(f"{PROJECT_SKILL_ROOT}/")


def utf8_safe(value: Any) -> Any:
    if isinstance(value, str):
        return value.encode("utf-8", "replace").decode("utf-8")
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, dict):
        return {
            utf8_safe(key) if isinstance(key, (str, bytes)) else key: utf8_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [utf8_safe(item) for item in value]
    return value


async def project_skill_validation_exception_handler(
    request: Request,
    error: RequestValidationError,
) -> Response:
    if not _is_project_skill_path(request.url.path):
        return await request_validation_exception_handler(request, error)
    content = jsonable_encoder(utf8_safe({"detail": error.errors()}))
    return JSONResponse(status_code=422, content=content)
