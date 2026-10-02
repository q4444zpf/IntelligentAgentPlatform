"""Persist control execution recovery and immutable feedback policy snapshots.

Revision ID: 20260915_31
Revises: 20260914_30
"""

import hashlib
import json

from alembic import op
import sqlalchemy as sa


revision = "20260915_31"
down_revision = "20260914_30"
branch_labels = None
depends_on = None

_OLD_STATUSES = "'pending_approval','approved','rejected','expired','executing','succeeded','failed','unknown','deviation','rollback_pending','rolled_back'"


def upgrade() -> None:
    with op.batch_alter_table("control_commands") as batch:
        batch.alter_column("team_version", new_column_name="team_version_id", existing_type=sa.String(64))
        batch.add_column(sa.Column("policy_digest", sa.String(64), nullable=True))
        batch.add_column(sa.Column("deviation_thresholds_snapshot", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("feedback_observed_at", sa.DateTime(timezone=True), nullable=True))
        batch.drop_constraint("ck_control_command_status", type_="check")
        batch.create_check_constraint("ck_control_command_status", f"status IN ({_OLD_STATUSES},'awaiting_feedback')")
    with op.batch_alter_table("control_execution_attempts") as batch:
        batch.add_column(sa.Column("external_id", sa.String(160), nullable=True))
        batch.add_column(sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
        batch.drop_constraint("uq_control_execution_request", type_="unique")
        batch.create_unique_constraint("uq_control_execution_request", ["command_id", "kind", "request_key"])

    connection = op.get_bind()
    metadata = sa.MetaData()
    commands = sa.Table("control_commands", metadata, autoload_with=connection)
    policies = sa.Table("control_policies", metadata, autoload_with=connection)
    attempts = sa.Table("control_execution_attempts", metadata, autoload_with=connection)
    # Legacy execution may have reached hardware without persisting replay evidence.
    connection.execute(commands.update().where(
        commands.c.status == "executing",
        ~sa.exists(sa.select(attempts.c.id).where(attempts.c.command_id == commands.c.id)),
    ).values(status="unknown"))
    snapshot_fields = (
        "asset_type", "command_type", "parameter_schema", "max_command_lifetime_seconds",
        "approval_mode", "deviation_thresholds", "enabled",
    )
    # Legacy commands have no historical snapshot; freeze the policy available at migration.
    for policy in connection.execute(sa.select(policies)).mappings():
        snapshot = {key: policy[key] for key in snapshot_fields}
        digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        connection.execute(commands.update().where(commands.c.policy_id == policy["id"]).values(
            policy_digest=digest, deviation_thresholds_snapshot=policy["deviation_thresholds"],
        ))
    for command in connection.execute(sa.select(commands)).mappings():
        if command["status"] == "succeeded" and command["actual_state"] is None:
            connection.execute(commands.update().where(commands.c.id == command["id"]).values(status="awaiting_feedback"))
        elif command["actual_state"] is not None:
            connection.execute(commands.update().where(commands.c.id == command["id"]).values(feedback_observed_at=command["updated_at"]))
    for attempt in connection.execute(sa.select(attempts)).mappings():
        summary = attempt["result_summary"] or {}
        if attempt["outcome"] != "pending" and not (attempt["outcome"] == "unknown" and "error" in summary):
            connection.execute(attempts.update().where(attempts.c.id == attempt["id"]).values(
                completed_at=attempt["created_at"], external_id=summary.get("external_id"),
            ))
    with op.batch_alter_table("control_commands") as batch:
        batch.alter_column("policy_digest", existing_type=sa.String(64), nullable=False)
        batch.alter_column("deviation_thresholds_snapshot", existing_type=sa.JSON(), nullable=False)

    op.create_table(
        "control_feedback_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("command_id", sa.String(36), sa.ForeignKey("control_commands.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recorder_id", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actual_state", sa.JSON(), nullable=False),
        sa.Column("deviation_result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_control_feedback_records_command_id", "control_feedback_records", ["command_id"])


def downgrade() -> None:
    # An older schema cannot faithfully represent new feedback history or scoped keys.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT COUNT(*) FROM control_feedback_records")):
        raise RuntimeError("cannot downgrade while control feedback records exist")
    if connection.scalar(sa.text("SELECT COUNT(*) FROM control_commands WHERE status = 'awaiting_feedback'")):
        raise RuntimeError("cannot downgrade while commands await feedback")
    if connection.execute(sa.text(
        "SELECT command_id FROM control_execution_attempts GROUP BY command_id, request_key HAVING COUNT(*) > 1"
    )).first():
        raise RuntimeError("cannot downgrade kind-scoped execution request keys")
    op.drop_index("ix_control_feedback_records_command_id", table_name="control_feedback_records")
    op.drop_table("control_feedback_records")
    with op.batch_alter_table("control_execution_attempts") as batch:
        batch.drop_constraint("uq_control_execution_request", type_="unique")
        batch.create_unique_constraint("uq_control_execution_request", ["command_id", "request_key"])
        batch.drop_column("completed_at")
        batch.drop_column("external_id")
    with op.batch_alter_table("control_commands") as batch:
        batch.drop_constraint("ck_control_command_status", type_="check")
        batch.create_check_constraint("ck_control_command_status", f"status IN ({_OLD_STATUSES})")
        batch.alter_column("team_version_id", new_column_name="team_version", existing_type=sa.String(64))
        batch.drop_column("policy_digest")
        batch.drop_column("deviation_thresholds_snapshot")
        batch.drop_column("feedback_observed_at")
