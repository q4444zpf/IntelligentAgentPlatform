from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from .service import NAME_PATTERN


class StrictProjectSkillModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectSkillCreate(StrictProjectSkillModel):
    content: str = Field(min_length=1, max_length=200_000)


class ProjectSkillDraftUpdate(ProjectSkillCreate):
    expected_revision: int = Field(strict=True, gt=0)


class ProjectSkillPublish(StrictProjectSkillModel):
    expected_revision: int = Field(strict=True, gt=0)


class ProjectSkillAvailabilityUpdate(StrictProjectSkillModel):
    enabled: bool
    expected_revision: int = Field(strict=True, gt=0)


IdempotencyKey = Annotated[
    str,
    StringConstraints(
        strict=True, min_length=1, max_length=128, pattern=r"^[\x20-\x7e]+$"
    ),
]


def _validate_import_name(value: str) -> str:
    if not NAME_PATTERN.fullmatch(value):
        raise ValueError("Invalid skill name")
    return value


ImportSkillName = Annotated[str, AfterValidator(_validate_import_name)]


class ProjectSkillImportCreate(StrictProjectSkillModel):
    action: Literal["create"]
    source_name: ImportSkillName
    target_name: ImportSkillName | None = None


class ProjectSkillImportUpdate(StrictProjectSkillModel):
    action: Literal["update"]
    source_name: ImportSkillName
    skill_id: UUID
    expected_revision: int = Field(strict=True, gt=0)


class ProjectSkillImportSkip(StrictProjectSkillModel):
    action: Literal["skip"]
    source_name: ImportSkillName


ProjectSkillImportEntry = Annotated[
    ProjectSkillImportCreate | ProjectSkillImportUpdate | ProjectSkillImportSkip,
    Field(discriminator="action"),
]


class SkillImportItem(StrictProjectSkillModel):
    source_name: str
    action: Literal["create", "update", "skip"]
    skill_id: str | None
    name: str
    draft_revision: int | None


class SkillImportResult(StrictProjectSkillModel):
    items: list[SkillImportItem]
    created_count: int
    updated_count: int
    skipped_count: int


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
    enabled: bool
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
    enabled: bool
    package_digest: str
    object_key: str | None = None
    archive_sha256: str | None = None
    size_bytes: int | None = None
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
