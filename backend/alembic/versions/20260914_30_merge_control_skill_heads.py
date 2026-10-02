"""merge dispatch-control and Skill availability heads

Revision ID: 20260914_30
Revises: 20260913_28, 20260914_29
"""

from collections.abc import Sequence


revision: str = "20260914_30"
down_revision: tuple[str, str] = ("20260913_28", "20260914_29")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
