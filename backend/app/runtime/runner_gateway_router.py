from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_session

from .execution_snapshot import ExecutionSnapshotService
from .run_tokens import RunTokenClaims, RunTokenService
from .runner_gateway_auth import default_token_service, require_runner_action
from .runner_gateway_schemas import SnapshotResponse
from .runner_gateway_service import RunnerGatewayService


def default_snapshot_service(
    session: Session = Depends(get_session),
) -> ExecutionSnapshotService:
    return ExecutionSnapshotService(session, None, None)


def create_router(
    *,
    token_service_dependency: Callable[..., RunTokenService] = default_token_service,
    snapshot_service_dependency: Callable[..., ExecutionSnapshotService] = default_snapshot_service,
) -> APIRouter:
    router = APIRouter()
    snapshot_claims = require_runner_action(
        "snapshot.read", token_service_dependency
    )

    @router.get(
        "/runs/{run_id}/snapshot",
        response_model=SnapshotResponse,
    )
    def get_snapshot(
        run_id: str,
        claims: RunTokenClaims = Depends(snapshot_claims),
        snapshot_service: ExecutionSnapshotService = Depends(
            snapshot_service_dependency
        ),
    ) -> SnapshotResponse:
        return RunnerGatewayService(snapshot_service).get_snapshot(run_id, claims)

    return router


router = create_router()
