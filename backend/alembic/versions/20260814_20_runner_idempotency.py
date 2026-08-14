"""add runner checkpoint binding and idempotency

Revision ID: 20260814_20
Revises: 20260814_19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260814_20"
down_revision: str | None = "20260814_19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runtime_checkpoints",
        sa.Column("snapshot_digest", sa.String(64), nullable=True),
    )
    op.add_column(
        "runtime_checkpoints",
        sa.Column("idempotency_key", sa.String(200), nullable=True),
    )
    op.create_table(
        "runtime_runner_requests",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "run_id",
            "action",
            "idempotency_key",
            name="uq_runtime_runner_request_run_action_key",
        ),
    )
    op.create_index(
        "ix_runtime_runner_requests_run_id",
        "runtime_runner_requests",
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_runtime_runner_requests_run_id",
        table_name="runtime_runner_requests",
    )
    op.drop_table("runtime_runner_requests")
    op.drop_column("runtime_checkpoints", "idempotency_key")
    op.drop_column("runtime_checkpoints", "snapshot_digest")
