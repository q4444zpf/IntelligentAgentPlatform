from __future__ import annotations

import secrets
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .container_launcher import LauncherUnavailableError
from .container_policy import InvalidContainerPolicyError


class ContainerCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_path: str
    agent_version: str
    checkpoint_key: str
    deadline_at: str
    snapshot_id: str
    snapshot_digest: str
    gateway_url: str
    run_token: str = Field(repr=False)


def create_launcher_app(launcher: Any, *, runner_token: str) -> FastAPI:
    app = FastAPI(title="Sandbox Launcher", version="0.1.0")

    def authorize(authorization: str | None, run_id: str | None, expected_run_id: str | None = None) -> None:
        if not runner_token:
            raise HTTPException(status_code=503, detail="runner authentication is not configured")
        supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
        if not secrets.compare_digest(supplied, runner_token):
            raise HTTPException(status_code=401, detail="invalid runner credentials")
        if expected_run_id is not None and run_id != expected_run_id:
            raise HTTPException(status_code=403, detail="run scope violation")

    def auth_headers(authorization: str | None, x_run_id: str | None) -> None:
        authorize(authorization, x_run_id)

    def run_operation(operation):
        try:
            return operation()
        except InvalidContainerPolicyError as exc:
            raise HTTPException(status_code=422, detail="Invalid workspace path") from exc
        except LauncherUnavailableError as exc:
            if str(exc) == "container is not registered for run":
                raise HTTPException(status_code=404, detail="Run container was not found") from exc
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/health")
    def health(authorization: str | None = Header(default=None)) -> dict[str, str]:
        authorize(authorization, None)
        return {"status": "healthy"}

    @app.post("/runs/{run_id}/container")
    def create(
        run_id: str,
        request: ContainerCreateRequest,
        authorization: str | None = Header(default=None),
        x_run_id: str | None = Header(default=None),
    ) -> dict[str, Any]:
        auth_headers(authorization, x_run_id)
        authorize(authorization, x_run_id, run_id)
        return run_operation(lambda: launcher.create(run_id, request.model_dump()))

    @app.get("/runs/{run_id}/container")
    def inspect(
        run_id: str,
        authorization: str | None = Header(default=None),
        x_run_id: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization, x_run_id, run_id)
        return run_operation(lambda: launcher.inspect(run_id))

    @app.post("/runs/{run_id}/container/terminate")
    def terminate(
        run_id: str,
        authorization: str | None = Header(default=None),
        x_run_id: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization, x_run_id, run_id)
        return run_operation(lambda: launcher.terminate(run_id))

    @app.delete("/runs/{run_id}/container")
    def cleanup(
        run_id: str,
        authorization: str | None = Header(default=None),
        x_run_id: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization, x_run_id, run_id)
        return run_operation(lambda: launcher.cleanup(run_id))

    return app
