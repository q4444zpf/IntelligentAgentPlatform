"""persist selected Team version on AgentRun

Revision ID: 20260817_22
Revises: 20260816_21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260817_22"
down_revision: str | None = "20260816_21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("actor_version_id", sa.String(36), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "actor_version_id")
