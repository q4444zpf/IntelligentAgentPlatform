from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TeamMemberDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1, max_length=64)
    responsibility: str = Field(min_length=1, max_length=500)
    tool_ids: list[str] = Field(default_factory=list)
    skill_names: list[str] = Field(default_factory=list)
    knowledge_source_ids: list[str] = Field(default_factory=list)


class TeamDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supervisor: TeamMemberDraft
    members: list[TeamMemberDraft] = Field(min_length=1)
    tool_ids: list[str] = Field(default_factory=list)
    skill_names: list[str] = Field(default_factory=list)
    knowledge_source_ids: list[str] = Field(default_factory=list)
    max_steps: int = Field(gt=0, le=100)
    max_parallel_members: int = Field(gt=0, le=32)
    timeout_seconds: int = Field(gt=0, le=86_400)
    failure_strategy: Literal["fail_fast", "continue_then_synthesize"] = "fail_fast"
    approval_policy_id: str | None = None


class TeamCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)


class TeamMetadataUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)


class TeamDraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1)
    draft: TeamDraft


class TeamMemberInfo(BaseModel):
    agent_id: str
    role: Literal["supervisor", "member"]
    responsibility: str
    agent_definition_digest: str | None


class TeamSummary(BaseModel):
    id: str
    unit_id: str
    project_id: str
    name: str
    description: str
    enabled: bool
    draft_revision: int
    published_version: int | None
    member_count: int
    supervisor: TeamMemberInfo | None
    updated_at: datetime


class TeamVersionInfo(BaseModel):
    id: str
    team_id: str
    version: int
    status: Literal["draft", "published"]
    definition: dict[str, Any]
    definition_digest: str | None
    members: list[TeamMemberInfo]
    max_steps: int
    max_parallel_members: int
    timeout_seconds: int
    failure_strategy: str
    published_by: str | None
    published_at: datetime | None


class ResolvedTeamRunActor(BaseModel):
    team_id: str
    version_id: str
    definition_digest: str
