"""add tool approval workflow

Revision ID: 20260810_15
Revises: 20260810_14
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260810_15"
down_revision: str | None = "20260810_14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("invocation_id", sa.String(36), sa.ForeignKey("tool_invocations.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("tool_id", sa.String(128), nullable=False),
        sa.Column("tool_version", sa.String(32), nullable=False),
        sa.Column("unit_id", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("requester_id", sa.String(64), nullable=False),
        sa.Column("requester_roles", sa.JSON(), nullable=False),
        sa.Column("assignee_role", sa.String(64), nullable=False, server_default="project_admin"),
        sa.Column("risk_level", sa.String(32), nullable=False, server_default="high"),
        sa.Column("arguments_summary", sa.JSON(), nullable=False),
        sa.Column("arguments_digest", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.String(64), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_approvals_run_id", "approvals", ["run_id"])
    op.create_index("ix_approvals_status", "approvals", ["status"])
    op.create_index("ix_approvals_scope_status", "approvals", ["unit_id", "project_id", "status"])
    op.create_index("ix_approvals_assignee_status", "approvals", ["assignee_role", "status"])


def downgrade() -> None:
    op.drop_index("ix_approvals_assignee_status", table_name="approvals")
    op.drop_index("ix_approvals_scope_status", table_name="approvals")
    op.drop_index("ix_approvals_status", table_name="approvals")
    op.drop_index("ix_approvals_run_id", table_name="approvals")
    op.drop_table("approvals")
