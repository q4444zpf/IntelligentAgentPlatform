"""add stable conversation message sequence

Revision ID: 20260801_04
Revises: 20260801_03
Create Date: 2026-08-01
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260801_04"
down_revision: str | None = "20260801_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("sequence", sa.Integer(), nullable=True))
    op.execute(
        """
        WITH ranked AS (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY conversation_id ORDER BY created_at, id
            ) AS message_sequence
            FROM messages
        )
        UPDATE messages
        SET sequence = ranked.message_sequence
        FROM ranked
        WHERE messages.id = ranked.id
        """
    )
    op.alter_column("messages", "sequence", nullable=False)
    op.create_unique_constraint(
        "uq_message_conversation_sequence",
        "messages",
        ["conversation_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_message_conversation_sequence", "messages", type_="unique"
    )
    op.drop_column("messages", "sequence")
