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


class ModelMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str = Field(min_length=1, max_length=20)
    content: str | None = None


class ModelToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_id: str = Field(min_length=1, max_length=128)
    description: str = ""
    input_schema: dict[str, Any]


class ModelInvocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str | None = None
    model: str | None = None
    messages: list[ModelMessage] = Field(min_length=1)
    tools: list[ModelToolDefinition] = Field(default_factory=list)
    temperature: float | None = Field(default=None, allow_inf_nan=False)
    max_output_tokens: int | None = Field(default=None, gt=0)
    invocation_sequence: int = Field(ge=0)


class ModelToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    arguments: dict[str, Any]


class ModelInvocationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    tool_calls: list[ModelToolCall] = Field(default_factory=list)


class ToolInvocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call_id: str = Field(min_length=1, max_length=128)
    tool_id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=32)
    arguments: dict[str, Any]
    invocation_sequence: int = Field(ge=0)


class ToolInvocationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invocation_id: str
    value: dict[str, Any]
