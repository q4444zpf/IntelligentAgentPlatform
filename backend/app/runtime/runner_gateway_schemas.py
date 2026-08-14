from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .execution_snapshot import ExecutionSnapshotPayload


class SnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    run_id: str
    digest: str
    payload: ExecutionSnapshotPayload


class CheckpointWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: dict[str, Any]


class CheckpointResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint_key: str
    snapshot_digest: str
    state: dict[str, Any]


class EventAppendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    event_type: str = Field(min_length=1, max_length=80)
    payload: dict[str, Any]


class EventAppendResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int
    runner_sequence: int
    event_type: str
