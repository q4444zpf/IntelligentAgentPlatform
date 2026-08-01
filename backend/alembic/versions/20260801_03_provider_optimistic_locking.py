"""add optimistic locking to provider configuration

Revision ID: 20260801_03
Revises: 20260801_02
Create Date: 2026-08-01
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260801_03"
down_revision: str | None = "20260801_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("provider_configs", "custom_providers", "platform_settings"):
        op.add_column(table, sa.Column("version", sa.Integer(), server_default="1", nullable=False))


def downgrade() -> None:
    for table in ("platform_settings", "custom_providers", "provider_configs"):
        op.drop_column(table, "version")
