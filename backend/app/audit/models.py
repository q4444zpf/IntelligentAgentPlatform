import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def new_id() -> str:
    return str(uuid.uuid4())


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            "authorization_scope IN ('platform','unit','project','own','emergency','system')",
            name="ck_audit_authorization_scope",
        ),
        CheckConstraint(
            "event_scope IN ('platform','unit','project')",
            name="ck_audit_event_scope",
        ),
        CheckConstraint(
            "(event_scope = 'platform' AND unit_id IS NULL AND project_id IS NULL) OR "
            "(event_scope = 'unit' AND unit_id IS NOT NULL AND project_id IS NULL) OR "
            "(event_scope = 'project' AND unit_id IS NOT NULL AND project_id IS NOT NULL)",
            name="ck_audit_event_scope_ids",
        ),
        CheckConstraint(
            "category IN ('runtime','management','security')",
            name="ck_audit_category",
        ),
        CheckConstraint(
            "source IN ('agent','tool','mcp','knowledge','sandbox','llm','system','auth')",
            name="ck_audit_source",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_audit_idempotency_key",
        ),
        Index("ix_audit_unit_time", "unit_id", "occurred_at", "id"),
        Index(
            "ix_audit_project_time",
            "unit_id",
            "project_id",
            "occurred_at",
            "id",
        ),
        Index(
            "ix_audit_user_time",
            "unit_id",
            "project_id",
            "user_id",
            "occurred_at",
            "id",
        ),
        Index("ix_audit_trace_time", "trace_id", "occurred_at", "id"),
        Index("ix_audit_run_time", "run_id", "occurred_at", "id"),
        Index(
            "ix_audit_source_action_status",
            "source",
            "action",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    unit_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_roles_json: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    authorization_scope: Mapped[str] = mapped_column(String(20), nullable=False)
    event_scope: Mapped[str] = mapped_column(String(20), nullable=False)
    auth_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    parent_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    resource_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resource_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
