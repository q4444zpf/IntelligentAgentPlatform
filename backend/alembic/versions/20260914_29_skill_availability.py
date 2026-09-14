"""Add authoritative Skill availability.

Revision ID: 20260914_29
Revises: 20260908_27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260914_29"
down_revision: str | None = "20260908_27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "skills",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column("skills", "enabled", server_default=None)


def downgrade() -> None:
    op.drop_column("skills", "enabled")
