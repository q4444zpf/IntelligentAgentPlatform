"""add MCP source mapping to the tool registry

Revision ID: 20260809_13
Revises: 20260808_12
Create Date: 2026-08-09
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260809_13"
down_revision: str | None = "20260808_12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "registered_tools",
        sa.Column("source_resource_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "registered_tools",
        sa.Column("source_capability_id", sa.String(256), nullable=True),
    )
    op.add_column(
        "registered_tools",
        sa.Column(
            "source_available",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("registered_tools", "source_available")
    op.drop_column("registered_tools", "source_capability_id")
    op.drop_column("registered_tools", "source_resource_id")
