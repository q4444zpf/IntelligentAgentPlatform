"""enforce case-insensitive uniqueness for user emails

Revision ID: 20260808_12
Revises: 20260808_11
Create Date: 2026-08-08
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260808_12"
down_revision: str | None = "20260808_11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_users_email_ci",
        "users",
        [sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
        sqlite_where=sa.text("email IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_users_email_ci", table_name="users")
