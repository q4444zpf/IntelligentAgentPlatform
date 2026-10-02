"""Persist policy revisions and command policy snapshots.

Revision ID: 20261002_32
Revises: 20260915_31
"""

from alembic import op
import sqlalchemy as sa


revision = "20261002_32"
down_revision = "20260915_31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("control_policies") as batch:
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    with op.batch_alter_table("control_commands") as batch:
        batch.add_column(sa.Column("policy_snapshot", sa.JSON(), nullable=True))
    connection = op.get_bind()
    commands = sa.table("control_commands", sa.column("id"), sa.column("policy_id"), sa.column("policy_snapshot", sa.JSON()))
    policies = sa.table("control_policies", sa.column("id"), sa.column("asset_type"), sa.column("command_type"), sa.column("parameter_schema", sa.JSON()), sa.column("max_command_lifetime_seconds"), sa.column("approval_mode"), sa.column("deviation_thresholds", sa.JSON()), sa.column("enabled"))
    for policy in connection.execute(sa.select(policies)).mappings():
        snapshot = {key: policy[key] for key in ("asset_type", "command_type", "parameter_schema", "max_command_lifetime_seconds", "approval_mode", "deviation_thresholds", "enabled")}
        connection.execute(commands.update().where(commands.c.policy_id == policy["id"]).values(policy_snapshot=snapshot))
    with op.batch_alter_table("control_commands") as batch:
        batch.alter_column("policy_snapshot", existing_type=sa.JSON(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("control_commands") as batch:
        batch.drop_column("policy_snapshot")
    with op.batch_alter_table("control_policies") as batch:
        batch.drop_column("revision")
