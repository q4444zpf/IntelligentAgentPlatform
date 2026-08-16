from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def new_id() -> str:
    return str(uuid4())


class TeamVersionImmutableError(ValueError):
    pass


class Team(Base):
    __tablename__ = "collaboration_teams"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_collaboration_teams_project_name"),
        Index("ix_collaboration_teams_unit_project", "unit_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    unit_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    draft_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    published_version_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "collaboration_team_versions.id",
            name="fk_collaboration_teams_published_version",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    versions: Mapped[list["TeamVersion"]] = relationship(
        back_populates="team",
        cascade="all, delete-orphan",
        foreign_keys="TeamVersion.team_id",
    )


class TeamVersion(Base):
    __tablename__ = "collaboration_team_versions"
    __table_args__ = (
        UniqueConstraint("team_id", "version", name="uq_collaboration_team_versions_team_version"),
        CheckConstraint("status IN ('draft', 'published')", name="ck_collaboration_team_versions_status"),
        CheckConstraint(
            "(status = 'draft' AND version = 0) OR (status = 'published' AND version > 0)",
            name="ck_collaboration_team_versions_status_version",
        ),
        CheckConstraint("max_steps > 0", name="ck_collaboration_team_versions_max_steps"),
        CheckConstraint(
            "max_parallel_members > 0",
            name="ck_collaboration_team_versions_max_parallel_members",
        ),
        CheckConstraint(
            "timeout_seconds > 0", name="ck_collaboration_team_versions_timeout_seconds",
        ),
        CheckConstraint(
            "status = 'draft' OR definition_digest IS NOT NULL",
            name="ck_collaboration_team_versions_published_digest",
        ),
        Index("ix_collaboration_team_versions_team_status", "team_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    team_id: Mapped[str] = mapped_column(
        ForeignKey("collaboration_teams.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    tool_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    skill_names: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    knowledge_source_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_parallel_members: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    failure_strategy: Mapped[str] = mapped_column(String(64), nullable=False, default="fail_fast")
    approval_policy_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    definition_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    published_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    team: Mapped[Team] = relationship(back_populates="versions", foreign_keys=[team_id])
    members: Mapped[list["TeamVersionMember"]] = relationship(
        back_populates="team_version",
        cascade="all, delete-orphan",
        order_by="TeamVersionMember.position",
    )


class TeamVersionMember(Base):
    __tablename__ = "collaboration_team_version_members"
    __table_args__ = (
        UniqueConstraint(
            "team_version_id", "agent_id", name="uq_collaboration_team_members_version_agent"
        ),
        UniqueConstraint(
            "team_version_id", "position", name="uq_collaboration_team_members_version_position"
        ),
        CheckConstraint("role IN ('supervisor', 'member')", name="ck_collaboration_team_members_role"),
        CheckConstraint("position >= 0", name="ck_collaboration_team_members_position"),
        Index("ix_collaboration_team_members_version", "team_version_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    team_version_id: Mapped[str] = mapped_column(
        ForeignKey("collaboration_team_versions.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_definition_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_definition: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    responsibility: Mapped[str] = mapped_column(String(500), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    skill_names: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    knowledge_source_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    team_version: Mapped[TeamVersion] = relationship(back_populates="members")


@event.listens_for(TeamVersion, "before_update")
def reject_published_team_version_updates(mapper, connection, target: TeamVersion) -> None:
    if target.status == "published":
        raise TeamVersionImmutableError("Published team versions are immutable")
