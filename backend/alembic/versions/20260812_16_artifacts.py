"""add artifact metadata

Revision ID: 20260812_16
Revises: 20260810_15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_16"
down_revision: str | None = "20260810_15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("unit_id", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False, server_default="project"),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("object_key", sa.Text(), nullable=False, unique=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(160), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_artifacts_scope", "artifacts", ["unit_id", "project_id", "owner_id", "status"])
    op.create_index("ix_artifacts_run", "artifacts", ["run_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_artifacts_run", table_name="artifacts")
    op.drop_index("ix_artifacts_scope", table_name="artifacts")
    op.drop_table("artifacts")
