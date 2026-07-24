from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class SkillCreateRequest(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    description: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=200_000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    enabled: bool = True

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(tag.strip() for tag in value if tag.strip()))


class SkillUpdateRequest(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=200_000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    enabled: bool = True


class SkillInfo(BaseModel):
    name: str
    description: str
    version: str = ""
    content: str
    source: Literal["created", "imported"] = "created"
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    file_count: int = 1
    updated_at: datetime


class SkillImportResponse(BaseModel):
    imported: list[str]
    skipped: list[str]
    count: int
    skills: list[SkillInfo]
