"""add runtime checkpoints

Revision ID: 20260812_17
Revises: 20260812_16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_17"
down_revision: str | None = "20260812_16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_checkpoints",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("checkpoint_key", sa.String(128), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "checkpoint_key", name="uq_runtime_checkpoint_run_key"),
    )
    op.create_index("ix_runtime_checkpoints_run_id", "runtime_checkpoints", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_runtime_checkpoints_run_id", table_name="runtime_checkpoints")
    op.drop_table("runtime_checkpoints")
