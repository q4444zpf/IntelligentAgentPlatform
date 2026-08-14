from __future__ import annotations

import os
from collections.abc import Callable

from fastapi import Depends, Header
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.config import RunnerTokenSettings
from app.core.database import get_session

from .run_tokens import (
    RunnerAction,
    RunTokenClaims,
    RunTokenForbidden,
    RunTokenInvalid,
    RunTokenNotFound,
    RunTokenService,
)


class RunnerGatewayError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


async def runner_gateway_error_handler(_request, error: RunnerGatewayError):
    return JSONResponse(
        status_code=error.status_code,
        content={
            **error.details,
            "code": error.code,
            "message": error.message,
        },
    )


def default_token_service(session: Session = Depends(get_session)) -> RunTokenService:
    return RunTokenService.from_settings(session, RunnerTokenSettings.from_env())


def validate_runner_gateway_startup() -> None:
    enabled = os.getenv(
        "IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED", "false"
    ).lower() in {"1", "true", "yes"}
    if enabled:
        RunnerTokenSettings.from_env()


def parse_bearer_token(authorization: str) -> str:
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not token.strip():
        raise RunnerGatewayError(401, "run_token_invalid", "Runner 凭证无效")
    return token.strip()


def require_runner_action(
    action: RunnerAction,
    token_service_dependency: Callable[..., RunTokenService] = default_token_service,
):
    def dependency(
        run_id: str,
        authorization: str = Header(default="", alias="Authorization"),
        token_service: RunTokenService = Depends(token_service_dependency),
    ) -> RunTokenClaims:
        token = parse_bearer_token(authorization)
        try:
            return token_service.verify(token, run_id, action)
        except RunTokenNotFound as error:
            raise RunnerGatewayError(404, "run_not_found", "Run 不存在") from error
        except RunTokenForbidden as error:
            raise RunnerGatewayError(
                403, "runner_action_forbidden", "Runner 操作未授权"
            ) from error
        except RunTokenInvalid as error:
            code = (
                "run_token_expired"
                if "expired" in str(error).lower()
                else "run_token_invalid"
            )
            message = "Runner 凭证已过期" if code == "run_token_expired" else "Runner 凭证无效"
            raise RunnerGatewayError(401, code, message) from error

    return dependency
