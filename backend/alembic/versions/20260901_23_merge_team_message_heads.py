"""merge published team and message run link heads

Revision ID: 20260901_23
Revises: 20260817_22, 20260828_21
"""

from collections.abc import Sequence

revision: str = "20260901_23"
down_revision: str | Sequence[str] | None = (
    "20260817_22",
    "20260828_21",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
