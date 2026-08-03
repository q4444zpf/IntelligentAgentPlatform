from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ConversationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ConversationInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    owner_id: str
    title: str
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=50_000)
    actor_type: Literal["agent", "team"]
    actor_id: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$"
    )


class MessageInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    created_at: datetime


class AgentRunInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    trigger_message_id: str
    actor_type: Literal["agent", "team"]
    actor_id: str
    status: str
    created_at: datetime
    updated_at: datetime


class AgentRunListItem(BaseModel):
    id: str
    conversation_id: str
    conversation_title: str
    trigger_message_id: str
    trigger_summary: str
    actor_type: Literal["agent", "team"]
    actor_id: str
    status: str
    tool_invocation_count: int
    duration_ms: int
    created_at: datetime
    updated_at: datetime


class AgentRunSummary(BaseModel):
    total: int
    completed: int
    running: int
    failed: int
    tool_invocations: int


class AgentRunPage(BaseModel):
    items: list[AgentRunListItem]
    page: int
    page_size: int
    total: int
    summary: AgentRunSummary


class MessageAccepted(BaseModel):
    message: MessageInfo
    run: AgentRunInfo


class ToolInvocationInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    tool_call_id: str
    tool_id: str
    tool_version: str
    status: str
    arguments_summary: dict[str, Any]
    result_summary: dict[str, Any] | None
    duration_ms: int | None
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None


class RunEventInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime
