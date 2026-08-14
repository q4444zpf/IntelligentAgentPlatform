from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.runtime.launcher_client import LauncherClientError, launcher_client_from_env
from app.runtime.sandbox_inspector import SandboxInspector
from app.runtime.sandbox_readiness import SandboxReadiness


def build_launcher_client_from_env():
    return launcher_client_from_env()


class RunSubmission(BaseModel):
    run_id: str
    agent_version: str
    checkpoint_key: str
    deadline_at: str
    snapshot_id: str
    snapshot_digest: str
    gateway_url: str
    run_token: str


def create_runner_app(*, sandbox_enabled: bool = False, readiness: SandboxReadiness | None = None, container_info: dict | None = None, inspect_transport=None, launcher_client=None) -> FastAPI:
    app = FastAPI(title="Workflow Runner", version="0.1.0")
    inspection_missing = False
    if readiness is None and inspect_transport is not None:
        container_info = inspect_transport("iap-run-current")
        inspection_missing = container_info is None
    readiness = readiness or (SandboxInspector().inspect(container_info) if container_info else SandboxReadiness(False, False, False, False, False, False))
    sandbox_ready = sandbox_enabled and readiness.is_ready()

    @app.get("/health")
    def health() -> dict[str, Any]:
        missing = readiness.missing()
        if inspection_missing:
            missing = ["container_inspection", *missing]
        return {"status": "healthy", "sandbox": sandbox_ready and not inspection_missing, "missing": missing}

    @app.post("/runs")
    def submit_run(request: RunSubmission) -> dict[str, str]:
        if not sandbox_ready:
            raise HTTPException(status_code=503, detail="Sandbox Executor is not enabled")
        if launcher_client is not None:
            try:
                launcher_client.prepare(
                    request.run_id,
                    agent_version=request.agent_version,
                    checkpoint_key=request.checkpoint_key,
                    deadline_at=request.deadline_at,
                    snapshot_id=request.snapshot_id,
                    snapshot_digest=request.snapshot_digest,
                    gateway_url=request.gateway_url,
                    run_token=request.run_token,
                )
            except LauncherClientError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"run_id": request.run_id, "status": "accepted"}

    def lifecycle(operation):
        if not sandbox_ready or launcher_client is None:
            raise HTTPException(status_code=503, detail="Sandbox Executor is not enabled")
        try:
            return operation()
        except LauncherClientError as exc:
            raise HTTPException(status_code=503, detail="Sandbox launcher is unavailable") from exc

    @app.get("/runs/{run_id}")
    def get_run_status(run_id: str) -> dict[str, Any]:
        return lifecycle(lambda: launcher_client.inspect(run_id))

    @app.post("/runs/{run_id}/terminate")
    def terminate_run(run_id: str) -> dict[str, Any]:
        return lifecycle(lambda: launcher_client.terminate(run_id))

    @app.delete("/runs/{run_id}")
    def cleanup_run(run_id: str) -> dict[str, Any]:
        return lifecycle(lambda: launcher_client.cleanup(run_id))

    return app


app = create_runner_app(
    sandbox_enabled=os.getenv("IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED", "false").lower()
    in {"1", "true", "yes"},
    readiness=SandboxReadiness(
        image_trusted=os.getenv("IAP_SANDBOX_IMAGE_TRUSTED", "false").lower() in {"1", "true", "yes"},
        non_root=os.getenv("IAP_SANDBOX_NON_ROOT", "false").lower() in {"1", "true", "yes"},
        read_only_root=os.getenv("IAP_SANDBOX_READ_ONLY_ROOT", "false").lower() in {"1", "true", "yes"},
        runner_gateway_network=os.getenv("IAP_SANDBOX_RUNNER_GATEWAY_NETWORK", "false").lower() in {"1", "true", "yes"},
        resource_limits=os.getenv("IAP_SANDBOX_RESOURCE_LIMITS", "false").lower() in {"1", "true", "yes"},
        cleanup_guaranteed=os.getenv("IAP_SANDBOX_CLEANUP_GUARANTEED", "false").lower() in {"1", "true", "yes"},
    ),
    launcher_client=build_launcher_client_from_env(),
)
