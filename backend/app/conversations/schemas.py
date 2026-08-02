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


class MessageAccepted(BaseModel):
    message: MessageInfo
    run: AgentRunInfo


class RunEventInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime
