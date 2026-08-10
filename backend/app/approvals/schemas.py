from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApprovalInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    invocation_id: str
    tool_id: str
    tool_version: str
    unit_id: str
    project_id: str
    requester_id: str
    requester_roles: list[str]
    assignee_role: str
    risk_level: str
    arguments_summary: dict[str, Any]
    arguments_digest: str
    status: Literal["pending", "approved", "rejected", "expired", "cancelled"]
    reason: str | None
    decided_by: str | None
    decision_reason: str | None
    expires_at: datetime
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApprovalDecisionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)
