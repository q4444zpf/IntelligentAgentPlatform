from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


RuntimeForm = Literal["web", "desktop", "common"]
ApprovalPolicy = Literal["never", "control_commands", "always"]


class AgentConfig(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    runtime_form: RuntimeForm = "common"
    language: Literal["zh-CN", "en-US"] = "zh-CN"
    provider_id: str = Field(default="", max_length=100)
    model: str = Field(default="", max_length=160)
    system_prompt: str = Field(default="", max_length=50_000)
    context_prompt: str = Field(default="", max_length=20_000)
    approval_policy: ApprovalPolicy = "control_commands"
    skill_names: list[str] = Field(default_factory=list, max_length=100)
    enabled: bool = True

    @field_validator("skill_names")
    @classmethod
    def normalize_skills(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(name.strip() for name in value if name.strip()))


class AgentCreateRequest(AgentConfig):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")


class AgentCopyRequest(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=120)
    copy_skills: bool = True


class AgentToggleRequest(BaseModel):
    enabled: bool


class AgentPinRequest(BaseModel):
    pinned: bool


class AgentDefaultRequest(BaseModel):
    agent_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")


class AgentInfo(AgentConfig):
    id: str
    pinned: bool = False
    is_builtin: bool
    is_default: bool
    startup_status: Literal["ready", "disabled"]
    workspace_dir: str
    created_at: datetime
    updated_at: datetime
