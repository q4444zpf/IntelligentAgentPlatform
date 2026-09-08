from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StrictProjectSkillModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkillFileInfo(StrictProjectSkillModel):
    path: str
    size: int
    sha256: str


class SkillSummary(StrictProjectSkillModel):
    id: str
    name: str
    description: str
    display_version: str
    draft_revision: int
    published_version_id: str | None
    created_at: datetime
    updated_at: datetime


class SkillDraftInfo(StrictProjectSkillModel):
    skill_id: str
    name: str
    description: str
    display_version: str
    revision: int
    content: str
    files: list[SkillFileInfo]
    package_digest: str
    updated_at: datetime


class PublishedSkillInfo(StrictProjectSkillModel):
    id: str
    skill_id: str
    version: int
    source_revision: int
    name: str
    description: str
    display_version: str
    package_digest: str
    published_by: str
    published_at: datetime


class SkillVersionInfo(PublishedSkillInfo):
    content: str
    files: list[SkillFileInfo]


class SkillPage(StrictProjectSkillModel):
    items: list[SkillSummary]
    total: int
    offset: int
    limit: int


class SkillVersionPage(StrictProjectSkillModel):
    items: list[PublishedSkillInfo]
    total: int
    offset: int
    limit: int


class ProjectSkillPageQuery(StrictProjectSkillModel):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class ProjectSkillListQuery(ProjectSkillPageQuery):
    q: str | None = Field(default=None, max_length=120)
