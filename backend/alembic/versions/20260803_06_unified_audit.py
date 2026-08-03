"""add unified audit schema

Revision ID: 20260803_06
Revises: 20260802_05
Create Date: 2026-08-03
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260803_06"
down_revision: str | None = "20260802_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("unit_id", sa.String(64), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE conversations SET unit_id = 'legacy-unit' "
            "WHERE unit_id IS NULL"
        )
    )
    op.alter_column(
        "conversations",
        "unit_id",
        existing_type=sa.String(64),
        nullable=False,
    )
    op.create_index(
        "ix_conversations_unit_project_owner",
        "conversations",
        ["unit_id", "project_id", "owner_id"],
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("unit_id", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=True),
        sa.Column("user_id", sa.String(64), nullable=True),
        sa.Column("actor_role", sa.String(40), nullable=True),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("risk_level", sa.String(20), nullable=False),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("run_id", sa.String(36), nullable=True),
        sa.Column("parent_event_id", sa.String(36), nullable=True),
        sa.Column("resource_type", sa.String(50), nullable=True),
        sa.Column("resource_id", sa.String(128), nullable=True),
        sa.Column("resource_name", sa.String(200), nullable=True),
        sa.Column(
            "summary",
            sa.Text(),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column(
            "metadata_json",
            sa.JSON(),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(120), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.String(180), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_audit_idempotency_key",
        ),
    )
    op.create_index(
        "ix_audit_unit_time",
        "audit_events",
        ["unit_id", "occurred_at", "id"],
    )
    op.create_index(
        "ix_audit_project_time",
        "audit_events",
        ["unit_id", "project_id", "occurred_at", "id"],
    )
    op.create_index(
        "ix_audit_user_time",
        "audit_events",
        ["unit_id", "project_id", "user_id", "occurred_at", "id"],
    )
    op.create_index(
        "ix_audit_trace_time",
        "audit_events",
        ["trace_id", "occurred_at", "id"],
    )
    op.create_index(
        "ix_audit_run_time",
        "audit_events",
        ["run_id", "occurred_at", "id"],
    )
    op.create_index(
        "ix_audit_source_action_status",
        "audit_events",
        ["source", "action", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_audit_source_action_status",
        table_name="audit_events",
    )
    op.drop_index("ix_audit_run_time", table_name="audit_events")
    op.drop_index("ix_audit_trace_time", table_name="audit_events")
    op.drop_index("ix_audit_user_time", table_name="audit_events")
    op.drop_index("ix_audit_project_time", table_name="audit_events")
    op.drop_index("ix_audit_unit_time", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index(
        "ix_conversations_unit_project_owner",
        table_name="conversations",
    )
    op.drop_column("conversations", "unit_id")
