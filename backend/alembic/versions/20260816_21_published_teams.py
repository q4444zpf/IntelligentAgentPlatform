"""add published team persistence

Revision ID: 20260816_21
Revises: 20260814_20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "20260816_21"
down_revision: str | None = "20260814_20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "collaboration_teams",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("unit_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("draft_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("published_version_id", sa.String(36), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("updated_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "name", name="uq_collaboration_teams_project_name"),
    )
    op.create_index(
        "ix_collaboration_teams_unit_project",
        "collaboration_teams",
        ["unit_id", "project_id"],
    )
    op.create_table(
        "collaboration_team_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("team_id", sa.String(36), sa.ForeignKey("collaboration_teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("tool_ids", sa.JSON(), nullable=False),
        sa.Column("skill_names", sa.JSON(), nullable=False),
        sa.Column("knowledge_source_ids", sa.JSON(), nullable=False),
        sa.Column("max_steps", sa.Integer(), nullable=False),
        sa.Column("max_parallel_members", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("failure_strategy", sa.String(64), nullable=False),
        sa.Column("approval_policy_id", sa.String(128), nullable=True),
        sa.Column("definition_digest", sa.String(64), nullable=True),
        sa.Column("published_by", sa.String(64), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('draft', 'published')", name="ck_collaboration_team_versions_status"),
        sa.CheckConstraint("(status = 'draft' AND version = 0) OR (status = 'published' AND version > 0)", name="ck_collaboration_team_versions_status_version"),
        sa.CheckConstraint("max_steps > 0", name="ck_collaboration_team_versions_max_steps"),
        sa.CheckConstraint("max_parallel_members > 0", name="ck_collaboration_team_versions_max_parallel_members"),
        sa.CheckConstraint("timeout_seconds > 0", name="ck_collaboration_team_versions_timeout_seconds"),
        sa.CheckConstraint("status = 'draft' OR definition_digest IS NOT NULL", name="ck_collaboration_team_versions_published_digest"),
        sa.UniqueConstraint("team_id", "version", name="uq_collaboration_team_versions_team_version"),
    )
    op.create_index(
        "ix_collaboration_team_versions_team_status",
        "collaboration_team_versions",
        ["team_id", "status"],
    )
    op.create_foreign_key(
        "fk_collaboration_teams_published_version",
        "collaboration_teams",
        "collaboration_team_versions",
        ["published_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "collaboration_team_version_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("team_version_id", sa.String(36), sa.ForeignKey("collaboration_team_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", sa.String(64), nullable=False),
        sa.Column("agent_definition_digest", sa.String(64), nullable=True),
        sa.Column("agent_definition", sa.JSON(), nullable=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("responsibility", sa.String(500), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("tool_ids", sa.JSON(), nullable=False),
        sa.Column("skill_names", sa.JSON(), nullable=False),
        sa.Column("knowledge_source_ids", sa.JSON(), nullable=False),
        sa.CheckConstraint("role IN ('supervisor', 'member')", name="ck_collaboration_team_members_role"),
        sa.CheckConstraint("position >= 0", name="ck_collaboration_team_members_position"),
        sa.UniqueConstraint("team_version_id", "agent_id", name="uq_collaboration_team_members_version_agent"),
        sa.UniqueConstraint("team_version_id", "position", name="uq_collaboration_team_members_version_position"),
    )
    op.create_index(
        "ix_collaboration_team_members_version",
        "collaboration_team_version_members",
        ["team_version_id"],
    )
    op.execute("""
        CREATE FUNCTION reject_published_team_version_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.status = 'published' THEN
                RAISE EXCEPTION 'published team versions are immutable';
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE FUNCTION reject_published_team_member_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM collaboration_team_versions
                WHERE id = OLD.team_version_id
                AND status = 'published'
            ) THEN
                RAISE EXCEPTION 'published team versions are immutable';
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER collaboration_team_versions_immutable
        BEFORE UPDATE OR DELETE ON collaboration_team_versions
        FOR EACH ROW EXECUTE FUNCTION reject_published_team_version_mutation()
    """)
    op.execute("""
        CREATE TRIGGER collaboration_team_version_members_immutable
        BEFORE UPDATE OR DELETE ON collaboration_team_version_members
        FOR EACH ROW EXECUTE FUNCTION reject_published_team_member_mutation()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS collaboration_team_version_members_immutable ON collaboration_team_version_members")
    op.execute("DROP TRIGGER IF EXISTS collaboration_team_versions_immutable ON collaboration_team_versions")
    op.execute("DROP FUNCTION IF EXISTS reject_published_team_member_mutation()")
    op.execute("DROP FUNCTION IF EXISTS reject_published_team_version_mutation()")
    op.drop_index("ix_collaboration_team_members_version", table_name="collaboration_team_version_members")
    op.drop_table("collaboration_team_version_members")
    op.drop_index("ix_collaboration_team_versions_team_status", table_name="collaboration_team_versions")
    op.drop_constraint(
        "fk_collaboration_teams_published_version",
        "collaboration_teams",
        type_="foreignkey",
    )
    op.drop_table("collaboration_team_versions")
    op.drop_index("ix_collaboration_teams_unit_project", table_name="collaboration_teams")
    op.drop_table("collaboration_teams")
