from __future__ import annotations

from .execution_snapshot import (
    ExecutionSnapshotService,
    SnapshotIntegrityError,
    verify_snapshot_digest,
)
from .run_tokens import RunTokenClaims
from .runner_gateway_auth import RunnerGatewayError
from .runner_gateway_schemas import SnapshotResponse


class RunnerGatewayService:
    def __init__(self, snapshot_service: ExecutionSnapshotService) -> None:
        self.snapshot_service = snapshot_service

    def get_snapshot(
        self, run_id: str, claims: RunTokenClaims
    ) -> SnapshotResponse:
        try:
            stored = self.snapshot_service.get(claims.snapshot_id)
        except SnapshotIntegrityError as error:
            raise RunnerGatewayError(
                409, "snapshot_invalid", "执行快照校验失败"
            ) from error
        if stored is None or stored.run_id != run_id:
            raise RunnerGatewayError(404, "run_not_found", "Run 不存在")
        if (
            stored.digest != claims.snapshot_digest
            or not verify_snapshot_digest(stored.payload, stored.digest)
        ):
            raise RunnerGatewayError(409, "snapshot_invalid", "执行快照校验失败")
        return SnapshotResponse(
            snapshot_id=stored.snapshot_id,
            run_id=stored.run_id,
            digest=stored.digest,
            payload=stored.payload,
        )
