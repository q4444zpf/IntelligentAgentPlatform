"""add run token revocations

Revision ID: 20260814_19
Revises: 20260814_18
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260814_19"
down_revision: str | None = "20260814_18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_run_token_revocations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("jti", sa.String(128), nullable=False, unique=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(120), nullable=False),
    )
    op.create_index(
        "ix_runtime_run_token_revocations_run_id",
        "runtime_run_token_revocations",
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_runtime_run_token_revocations_run_id",
        table_name="runtime_run_token_revocations",
    )
    op.drop_table("runtime_run_token_revocations")
