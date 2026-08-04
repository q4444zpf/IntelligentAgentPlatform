from datetime import datetime
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

AuditCategory: TypeAlias = Literal["runtime", "management"]
AuditSource: TypeAlias = Literal["agent", "tool", "mcp", "knowledge", "sandbox", "llm", "system"]
AuditStatus: TypeAlias = Literal["started", "succeeded", "failed", "cancelled"]
AuditRisk: TypeAlias = Literal["low", "medium", "high", "critical"]
AuditActorRole: TypeAlias = Literal[
    "unknown", "user", "project_admin", "unit_auditor",
    "project_admin,user", "project_admin,unit_auditor", "unit_auditor,user",
    "project_admin,unit_auditor,user",
]


class AuditEventListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    unit_id: str
    project_id: str | None
    user_id: str | None
    actor_role: AuditActorRole
    category: AuditCategory
    source: AuditSource
    action: str
    status: AuditStatus
    risk_level: AuditRisk
    trace_id: str | None
    run_id: str | None
    resource_type: str | None
    resource_id: str | None
    resource_name: str | None
    duration_ms: int | None
    occurred_at: datetime


class AuditEventDetail(AuditEventListItem):
    parent_event_id: str | None
    summary: str
    metadata: dict[str, Any] = Field(validation_alias="metadata_json")
    error_code: str | None
    created_at: datetime


class AuditSummary(BaseModel):
    total: int
    failed: int
    high_risk: int
    runtime: int
    management: int
    by_source: dict[AuditSource, int]


class AuditEventPage(BaseModel):
    items: list[AuditEventListItem]
    page: int
    page_size: int
    total: int
    summary: AuditSummary
