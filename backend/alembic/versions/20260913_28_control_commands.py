"""add dispatch control domain

Revision ID: 20260913_28
Revises: 20260908_27
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260913_28"
down_revision: str | None = "20260908_27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("collaboration_team_versions") as batch_op:
        batch_op.create_check_constraint(
            "ck_collaboration_team_versions_failure_strategy",
            "failure_strategy IN ('fail_fast', 'continue_then_synthesize')",
        )
    op.create_table("control_policies",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("unit_id", sa.String(64), nullable=False), sa.Column("project_id", sa.String(64), nullable=False), sa.Column("asset_type", sa.String(80), nullable=False), sa.Column("command_type", sa.String(80), nullable=False), sa.Column("parameter_schema", sa.JSON(), nullable=False), sa.Column("max_command_lifetime_seconds", sa.Integer(), nullable=False), sa.Column("approval_mode", sa.String(10), nullable=False, server_default="single"), sa.Column("deviation_thresholds", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("owner_id", sa.String(64), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.CheckConstraint("approval_mode IN ('single','dual')", name="ck_control_policy_approval_mode"), sa.CheckConstraint("max_command_lifetime_seconds > 0", name="ck_control_policy_lifetime"), sa.UniqueConstraint("unit_id", "project_id", "asset_type", "command_type", name="uq_control_policy_pair"))
    op.create_table("control_commands",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("unit_id", sa.String(64), nullable=False), sa.Column("project_id", sa.String(64), nullable=False), sa.Column("policy_id", sa.String(36), sa.ForeignKey("control_policies.id", ondelete="RESTRICT"), nullable=False), sa.Column("requester_id", sa.String(64), nullable=False), sa.Column("run_id", sa.String(36), nullable=True), sa.Column("team_version", sa.String(64), nullable=True), sa.Column("target", sa.JSON(), nullable=False), sa.Column("parameters", sa.JSON(), nullable=False), sa.Column("parameter_digest", sa.String(64), nullable=False), sa.Column("simulation_artifact_id", sa.String(36), sa.ForeignKey("artifacts.id", ondelete="RESTRICT"), nullable=False), sa.Column("simulation_digest", sa.String(64), nullable=False), sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False), sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False), sa.Column("required_approvals", sa.Integer(), nullable=False), sa.Column("rollback_plan", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("expected_state", sa.JSON(), nullable=True), sa.Column("actual_state", sa.JSON(), nullable=True), sa.Column("deviation_result", sa.JSON(), nullable=True), sa.Column("external_command_ref", sa.String(160), nullable=True), sa.Column("idempotency_key", sa.String(180), nullable=False), sa.Column("status", sa.String(32), nullable=False, server_default="pending_approval"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.CheckConstraint("status IN ('pending_approval','approved','rejected','expired','executing','succeeded','failed','unknown','deviation','rollback_pending','rolled_back')", name="ck_control_command_status"), sa.CheckConstraint("required_approvals IN (1,2) ", name="ck_control_command_required_approvals"), sa.CheckConstraint("valid_until > valid_from", name="ck_control_command_validity"), sa.UniqueConstraint("unit_id", "project_id", "idempotency_key", name="uq_control_command_idempotency"))
    op.create_table("control_command_approvals", sa.Column("id", sa.String(36), primary_key=True), sa.Column("command_id", sa.String(36), sa.ForeignKey("control_commands.id", ondelete="CASCADE"), nullable=False), sa.Column("approver_id", sa.String(64), nullable=False), sa.Column("decision", sa.String(16), nullable=False), sa.Column("reason", sa.Text(), nullable=True), sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.CheckConstraint("decision IN ('approve','reject')", name="ck_control_approval_decision"), sa.UniqueConstraint("command_id", "approver_id", name="uq_control_command_approver"))
    op.create_table("control_execution_attempts", sa.Column("id", sa.String(36), primary_key=True), sa.Column("command_id", sa.String(36), sa.ForeignKey("control_commands.id", ondelete="CASCADE"), nullable=False), sa.Column("kind", sa.String(16), nullable=False), sa.Column("request_key", sa.String(180), nullable=False), sa.Column("external_idempotency_key", sa.String(180), nullable=False), sa.Column("outcome", sa.String(24), nullable=False), sa.Column("result_summary", sa.JSON(), nullable=False, server_default=sa.text("'{}'")), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()), sa.CheckConstraint("kind IN ('execute','rollback')", name="ck_control_execution_kind"), sa.CheckConstraint("outcome IN ('pending','succeeded','failed','unknown')", name="ck_control_execution_outcome"), sa.UniqueConstraint("command_id", "request_key", name="uq_control_execution_request"), sa.UniqueConstraint("external_idempotency_key", name="uq_control_execution_external_key"))
    op.create_index("ix_control_policy_scope", "control_policies", ["unit_id", "project_id", "enabled"])
    op.create_index("ix_control_command_scope_status", "control_commands", ["unit_id", "project_id", "status"])
    op.create_index("ix_control_commands_status", "control_commands", ["status"])
    op.create_index("ix_control_command_approvals_command_id", "control_command_approvals", ["command_id"])
    op.create_index("ix_control_execution_attempts_command_id", "control_execution_attempts", ["command_id"])


def downgrade() -> None:
    with op.batch_alter_table("collaboration_team_versions") as batch_op:
        batch_op.drop_constraint(
            "ck_collaboration_team_versions_failure_strategy",
            type_="check",
        )
    op.drop_index("ix_control_execution_attempts_command_id", table_name="control_execution_attempts")
    op.drop_index("ix_control_command_approvals_command_id", table_name="control_command_approvals")
    op.drop_index("ix_control_commands_status", table_name="control_commands")
    op.drop_index("ix_control_command_scope_status", table_name="control_commands")
    op.drop_index("ix_control_policy_scope", table_name="control_policies")
    op.drop_table("control_execution_attempts")
    op.drop_table("control_command_approvals")
    op.drop_table("control_commands")
    op.drop_table("control_policies")
