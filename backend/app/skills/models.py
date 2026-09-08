from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SkillVersionImmutableError(ValueError):
    pass


class Skill(Base):
    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("unit_id", "project_id", "name", name="uq_skills_scope_name"),
        ForeignKeyConstraint(
            ["id", "published_version_id"], ["skill_versions.skill_id", "skill_versions.id"],
            name="fk_skills_owned_published_version", use_alter=True,
        ),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    unit_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    published_version_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PackageSnapshot:
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    display_version: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    files: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    package_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    archive_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)


class SkillDraft(PackageSnapshot, Base):
    __tablename__ = "skill_drafts"
    __table_args__ = (CheckConstraint("revision > 0", name="ck_skill_drafts_revision"),)

    skill_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("skills.id"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SkillVersion(PackageSnapshot, Base):
    __tablename__ = "skill_versions"
    __table_args__ = (
        UniqueConstraint("skill_id", "id", name="uq_skill_versions_skill_id"),
        UniqueConstraint("skill_id", "version", name="uq_skill_versions_number"),
        UniqueConstraint("skill_id", "idempotency_key", name="uq_skill_versions_idempotency"),
        CheckConstraint("version > 0", name="ck_skill_versions_number"),
        CheckConstraint("source_revision > 0", name="ck_skill_versions_source_revision"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    skill_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), ForeignKey("skills.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    published_by: Mapped[str] = mapped_column(String(128), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def enforce_skill_version_flush(session, flush_context, instances):
    if any(isinstance(item, SkillVersion) for item in session.deleted) or any(
        isinstance(item, SkillVersion) and session.is_modified(item, include_collections=True)
        for item in session.dirty
    ):
        raise SkillVersionImmutableError("Published skill versions are immutable")


def enforce_skill_version_execute(execute_state):
    if (execute_state.is_update or execute_state.is_delete) and getattr(
        getattr(execute_state.statement, "table", None), "name", None
    ) == SkillVersion.__tablename__:
        raise SkillVersionImmutableError("Published skill versions are immutable")
