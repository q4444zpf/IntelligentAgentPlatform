from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

_REFERENCE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
_ERROR_CODE_PATTERN = r"^[a-z][a-z0-9_]{0,119}$"


class RunExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=128, pattern=_REFERENCE_PATTERN)
    agent_version: str = Field(min_length=1, max_length=128, pattern=_REFERENCE_PATTERN)
    checkpoint_key: str = Field(min_length=1, max_length=128, pattern=_REFERENCE_PATTERN)
    deadline_at: datetime
    snapshot_id: str = Field(min_length=1, max_length=128, pattern=_REFERENCE_PATTERN)
    snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    gateway_url: str = Field(min_length=1, max_length=2048)
    run_token: str = Field(min_length=1, max_length=8192, repr=False)

    @field_validator("deadline_at")
    @classmethod
    def validate_deadline(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("deadline_at must include timezone information")
        normalized = value.astimezone(timezone.utc)
        if normalized <= datetime.now(timezone.utc):
            raise ValueError("deadline_at must be in the future")
        return normalized

    @field_validator("gateway_url")
    @classmethod
    def validate_gateway_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("gateway_url must be an HTTP(S) service URL")
        return value.rstrip("/")


class RunExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["completed", "failed", "cancelled", "interrupted"]
    error_code: str | None = Field(default=None, pattern=_ERROR_CODE_PATTERN)
    artifact_refs: tuple[str, ...] = ()
    checkpoint_key: str | None = Field(default=None, max_length=128, pattern=_REFERENCE_PATTERN)

    @field_validator("artifact_refs")
    @classmethod
    def validate_artifact_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > 100:
            raise ValueError("too many artifact references")
        for reference in value:
            if not reference or len(reference) > 128:
                raise ValueError("invalid artifact reference")
        return value
