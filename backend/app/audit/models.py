import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
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
    unit_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
