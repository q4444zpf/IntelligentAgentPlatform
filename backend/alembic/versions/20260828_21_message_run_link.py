"""link assistant messages to runs

Revision ID: 20260828_21
Revises: 20260814_20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260828_21"
down_revision: str | None = "20260814_20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("run_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_messages_run_id_agent_runs",
        "messages",
        "agent_runs",
        ["run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_messages_run_id", "messages", ["run_id"], unique=False)
    op.execute(sa.text("""
        UPDATE messages AS message
        SET run_id = event.run_id
        FROM run_events AS event
        WHERE event.event_type = 'message.completed'
          AND event.payload ->> 'message_id' = message.id
          AND message.role = 'assistant'
          AND message.run_id IS NULL
    """))


def downgrade() -> None:
    op.drop_index("ix_messages_run_id", table_name="messages")
    op.drop_constraint("fk_messages_run_id_agent_runs", "messages", type_="foreignkey")
    op.drop_column("messages", "run_id")
