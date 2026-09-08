"""Persist scoped skill drafts and immutable published versions.

Revision ID: 20260908_26
Revises: 20260902_25
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260908_26"
down_revision: str | None = "20260902_25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _snapshot_columns() -> list[sa.Column]:
    return [
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("display_version", sa.String(128), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("files", sa.JSON(), nullable=False),
        sa.Column("package_digest", sa.String(64), nullable=False),
        sa.Column("object_key", sa.String(512), nullable=False),
        sa.Column("archive_sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "skills",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("unit_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("published_version_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("unit_id", "project_id", "name", name="uq_skills_scope_name"),
    )
    op.create_table(
        "skill_drafts",
        sa.Column("skill_id", sa.Uuid(as_uuid=False), sa.ForeignKey("skills.id"), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        *_snapshot_columns(),
        sa.CheckConstraint("revision > 0", name="ck_skill_drafts_revision"),
    )
    op.create_table(
        "skill_versions",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("skill_id", sa.Uuid(as_uuid=False), sa.ForeignKey("skills.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("published_by", sa.String(128), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        *_snapshot_columns(),
        sa.UniqueConstraint("skill_id", "id", name="uq_skill_versions_skill_id"),
        sa.UniqueConstraint("skill_id", "version", name="uq_skill_versions_number"),
        sa.UniqueConstraint("skill_id", "idempotency_key", name="uq_skill_versions_idempotency"),
        sa.CheckConstraint("version > 0", name="ck_skill_versions_number"),
        sa.CheckConstraint("source_revision > 0", name="ck_skill_versions_source_revision"),
    )
    op.create_foreign_key(
        "fk_skills_owned_published_version", "skills", "skill_versions",
        ["id", "published_version_id"], ["skill_id", "id"],
    )
    op.execute("""
        CREATE FUNCTION reject_skill_version_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Published skill versions are immutable' USING ERRCODE = '55000';
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER skill_versions_immutable
        BEFORE UPDATE OR DELETE ON skill_versions
        FOR EACH ROW EXECUTE FUNCTION reject_skill_version_mutation()
    """)


def downgrade() -> None:
    op.drop_constraint("fk_skills_owned_published_version", "skills", type_="foreignkey")
    op.drop_table("skill_versions")
    op.execute("DROP FUNCTION reject_skill_version_mutation()")
    op.drop_table("skill_drafts")
    op.drop_table("skills")
