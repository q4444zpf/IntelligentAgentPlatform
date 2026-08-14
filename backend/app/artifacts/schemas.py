from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ArtifactScope = Literal["private", "project", "tenant", "public"]
ArtifactStatus = Literal["active", "deleted"]


class ArtifactCreateRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=160)
    size_bytes: int = Field(gt=0, le=1024 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope: ArtifactScope = "project"

    @field_validator("filename")
    @classmethod
    def reject_path_segments(cls, value: str) -> str:
        if value.strip() != value or value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("filename must be a base name")
        return value


class ArtifactInfo(BaseModel):
    id: str
    unit_id: str
    project_id: str | None
    owner_id: str
    scope: ArtifactScope
    run_id: str | None
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    status: ArtifactStatus
    created_at: datetime
    deleted_at: datetime | None


class ArtifactDownloadInfo(BaseModel):
    artifact: ArtifactInfo
    url: str
    expires_in: int
