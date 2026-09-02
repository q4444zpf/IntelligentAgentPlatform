"""add explicit Agent availability

Revision ID: 20260902_25
Revises: 20260901_24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260902_25"
down_revision: str | None = "20260901_24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "managed_agents",
        sa.Column("availability_scope", sa.String(16), nullable=True),
    )
    op.add_column(
        "managed_agents",
        sa.Column("unit_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "managed_agents",
        sa.Column("project_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "managed_agents",
        sa.Column("allowed_project_ids", sa.JSON(), nullable=True),
    )
    op.execute(
        "UPDATE managed_agents SET availability_scope = 'common', "
        "allowed_project_ids = '[\"*\"]'::json"
    )
    op.alter_column("managed_agents", "availability_scope", nullable=False)
    op.alter_column("managed_agents", "allowed_project_ids", nullable=False)
    op.create_check_constraint(
        "ck_managed_agents_availability_scope",
        "managed_agents",
        "availability_scope IN ('project', 'common')",
    )
    op.create_check_constraint(
        "ck_managed_agents_availability_shape",
        "managed_agents",
        "(availability_scope = 'project' AND unit_id IS NOT NULL AND project_id IS NOT NULL) "
        "OR (availability_scope = 'common' AND unit_id IS NULL AND project_id IS NULL)",
    )
    op.create_index(
        "ix_managed_agents_project_scope",
        "managed_agents",
        ["unit_id", "project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_managed_agents_project_scope", table_name="managed_agents")
    op.drop_constraint(
        "ck_managed_agents_availability_shape",
        "managed_agents",
        type_="check",
    )
    op.drop_constraint(
        "ck_managed_agents_availability_scope",
        "managed_agents",
        type_="check",
    )
    op.drop_column("managed_agents", "allowed_project_ids")
    op.drop_column("managed_agents", "project_id")
    op.drop_column("managed_agents", "unit_id")
    op.drop_column("managed_agents", "availability_scope")
