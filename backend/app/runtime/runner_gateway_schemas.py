from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .execution_snapshot import ExecutionSnapshotPayload


class SnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    run_id: str
    digest: str
    payload: ExecutionSnapshotPayload
