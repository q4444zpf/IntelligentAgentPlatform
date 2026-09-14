from datetime import datetime
from typing import Any
import uuid

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def new_id() -> str:
    return str(uuid.uuid4())


class ControlPolicy(Base):
    __tablename__ = "control_policies"
    __table_args__ = (
        CheckConstraint("approval_mode IN ('single','dual')", name="ck_control_policy_approval_mode"),
        CheckConstraint("max_command_lifetime_seconds > 0", name="ck_control_policy_lifetime"),
        UniqueConstraint("unit_id", "project_id", "asset_type", "command_type", name="uq_control_policy_pair"),
        Index("ix_control_policy_scope", "unit_id", "project_id", "enabled"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    unit_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(80), nullable=False)
    command_type: Mapped[str] = mapped_column(String(80), nullable=False)
    parameter_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    max_command_lifetime_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    approval_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="single")
    deviation_thresholds: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ControlCommand(Base):
    __tablename__ = "control_commands"
    __table_args__ = (
        CheckConstraint("status IN ('pending_approval','approved','rejected','expired','executing','succeeded','failed','unknown','deviation','rollback_pending','rolled_back')", name="ck_control_command_status"),
        CheckConstraint("required_approvals IN (1,2)", name="ck_control_command_required_approvals"),
        CheckConstraint("valid_until > valid_from", name="ck_control_command_validity"),
        UniqueConstraint("unit_id", "project_id", "idempotency_key", name="uq_control_command_idempotency"),
        Index("ix_control_command_scope_status", "unit_id", "project_id", "status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    unit_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_id: Mapped[str] = mapped_column(ForeignKey("control_policies.id", ondelete="RESTRICT"), nullable=False)
    requester_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    team_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    parameter_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    simulation_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id", ondelete="RESTRICT"), nullable=False)
    simulation_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    required_approvals: Mapped[int] = mapped_column(Integer, nullable=False)
    rollback_plan: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    expected_state: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    actual_state: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    deviation_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    external_command_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending_approval", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ControlCommandApproval(Base):
    __tablename__ = "control_command_approvals"
    __table_args__ = (CheckConstraint("decision IN ('approve','reject')", name="ck_control_approval_decision"), UniqueConstraint("command_id", "approver_id", name="uq_control_command_approver"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    command_id: Mapped[str] = mapped_column(ForeignKey("control_commands.id", ondelete="CASCADE"), nullable=False, index=True)
    approver_id: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ControlExecutionAttempt(Base):
    __tablename__ = "control_execution_attempts"
    __table_args__ = (CheckConstraint("kind IN ('execute','rollback')", name="ck_control_execution_kind"), CheckConstraint("outcome IN ('pending','succeeded','failed','unknown')", name="ck_control_execution_outcome"), UniqueConstraint("command_id", "request_key", name="uq_control_execution_request"), UniqueConstraint("external_idempotency_key", name="uq_control_execution_external_key"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    command_id: Mapped[str] = mapped_column(ForeignKey("control_commands.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    request_key: Mapped[str] = mapped_column(String(180), nullable=False)
    external_idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
