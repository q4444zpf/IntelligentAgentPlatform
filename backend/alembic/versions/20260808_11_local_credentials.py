"""add local account credentials

Revision ID: 20260808_11
Revises: 20260804_10
Create Date: 2026-08-08
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260808_11"
down_revision: str | None = "20260804_10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "local_credentials",
        sa.Column("user_id", sa.String(36), primary_key=True),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("failed_attempts >= 0", name="ck_local_credentials_failed_attempts"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.drop_constraint("ck_auth_sessions_method", "auth_sessions", type_="check")
    op.create_check_constraint(
        "ck_auth_sessions_method",
        "auth_sessions",
        "auth_method IN ('oidc','dev_test','local')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_auth_sessions_method", "auth_sessions", type_="check")
    op.create_check_constraint(
        "ck_auth_sessions_method",
        "auth_sessions",
        "auth_method IN ('oidc','dev_test')",
    )
    op.drop_table("local_credentials")
