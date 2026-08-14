"""add immutable execution snapshots

Revision ID: 20260814_18
Revises: 20260812_17
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260814_18"
down_revision: str | None = "20260812_17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_execution_snapshots",
        sa.Column("snapshot_id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_runtime_execution_snapshots_run_id",
        "runtime_execution_snapshots",
        ["run_id"],
        unique=True,
    )
    op.execute("""
        CREATE FUNCTION reject_runtime_execution_snapshot_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'runtime execution snapshots are immutable';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER runtime_execution_snapshots_immutable
        BEFORE UPDATE ON runtime_execution_snapshots
        FOR EACH ROW
        EXECUTE FUNCTION reject_runtime_execution_snapshot_update()
    """)


def downgrade() -> None:
    op.execute("""
        DROP TRIGGER IF EXISTS runtime_execution_snapshots_immutable
        ON runtime_execution_snapshots
    """)
    op.execute("DROP FUNCTION IF EXISTS reject_runtime_execution_snapshot_update()")
    op.drop_index(
        "ix_runtime_execution_snapshots_run_id",
        table_name="runtime_execution_snapshots",
    )
    op.drop_table("runtime_execution_snapshots")
