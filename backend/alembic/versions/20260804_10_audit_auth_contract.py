"""migrate audit identity snapshots and authentication events

Revision ID: 20260804_10
Revises: 20260804_09
Create Date: 2026-08-04
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260804_10"
down_revision: str | None = "20260804_09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AUTHORIZATION_SCOPES = "'platform','unit','project','own','emergency','system'"
_EVENT_SCOPES = "'platform','unit','project'"
_CATEGORIES = "'runtime','management','security'"
_SOURCES = "'agent','tool','mcp','knowledge','sandbox','llm','system','auth'"


def _legacy_role_snapshot(column: str) -> str:
    # Roles introduced after the legacy contract cannot be represented safely.
    return f"""
        CASE
            WHEN {column}::jsonb = '[]'::jsonb THEN 'unknown'
            WHEN {column}::jsonb = '["user"]'::jsonb THEN 'user'
            WHEN {column}::jsonb = '["project_admin"]'::jsonb
                THEN 'project_admin'
            WHEN {column}::jsonb = '["unit_auditor"]'::jsonb
                THEN 'unit_auditor'
            WHEN {column}::jsonb = '["project_admin","user"]'::jsonb
                THEN 'project_admin,user'
            WHEN {column}::jsonb = '["project_admin","unit_auditor"]'::jsonb
                THEN 'project_admin,unit_auditor'
            WHEN {column}::jsonb = '["unit_auditor","user"]'::jsonb
                THEN 'unit_auditor,user'
            WHEN {column}::jsonb = '["project_admin","unit_auditor","user"]'::jsonb
                THEN 'project_admin,unit_auditor,user'
            ELSE 'unknown'
        END
    """


def upgrade() -> None:
    op.add_column(
        "audit_events",
        sa.Column(
            "actor_roles_json",
            sa.JSON(),
            nullable=True,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.add_column(
        "audit_events",
        sa.Column("authorization_scope", sa.String(20), nullable=True),
    )
    op.add_column(
        "audit_events",
        sa.Column("event_scope", sa.String(20), nullable=True),
    )
    op.add_column(
        "audit_events",
        sa.Column("auth_method", sa.String(20), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "actor_roles_json",
            sa.JSON(),
            nullable=True,
            server_default=sa.text("'[]'::json"),
        ),
    )

    op.execute(sa.text("""
        UPDATE audit_events
        SET actor_roles_json = CASE
            WHEN actor_role = 'unknown' THEN '[]'::json
            ELSE to_json(string_to_array(actor_role, ','))
        END,
        authorization_scope = CASE
            WHEN project_id IS NOT NULL THEN 'project'
            ELSE 'unit'
        END,
        event_scope = CASE
            WHEN project_id IS NOT NULL THEN 'project'
            ELSE 'unit'
        END
    """))
    op.execute(sa.text("""
        UPDATE agent_runs
        SET actor_roles_json = CASE
            WHEN actor_role = 'unknown' THEN '[]'::json
            ELSE to_json(string_to_array(actor_role, ','))
        END
    """))

    op.drop_constraint("ck_audit_actor_role", "audit_events", type_="check")
    op.drop_column("audit_events", "actor_role")
    op.drop_column("agent_runs", "actor_role")
    op.alter_column(
        "audit_events", "actor_roles_json", nullable=False, server_default=None,
    )
    op.alter_column("audit_events", "authorization_scope", nullable=False)
    op.alter_column("audit_events", "event_scope", nullable=False)
    op.alter_column(
        "agent_runs", "actor_roles_json", nullable=False, server_default=None,
    )
    op.alter_column(
        "audit_events",
        "unit_id",
        existing_type=sa.String(64),
        nullable=True,
    )

    op.create_check_constraint(
        "ck_audit_authorization_scope",
        "audit_events",
        f"authorization_scope IN ({_AUTHORIZATION_SCOPES})",
    )
    op.create_check_constraint(
        "ck_audit_event_scope",
        "audit_events",
        f"event_scope IN ({_EVENT_SCOPES})",
    )
    op.create_check_constraint(
        "ck_audit_event_scope_ids",
        "audit_events",
        "(event_scope = 'platform' AND unit_id IS NULL AND project_id IS NULL) OR "
        "(event_scope = 'unit' AND unit_id IS NOT NULL AND project_id IS NULL) OR "
        "(event_scope = 'project' AND unit_id IS NOT NULL AND project_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_audit_category",
        "audit_events",
        f"category IN ({_CATEGORIES})",
    )
    op.create_check_constraint(
        "ck_audit_source",
        "audit_events",
        f"source IN ({_SOURCES})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_audit_source", "audit_events", type_="check")
    op.drop_constraint("ck_audit_category", "audit_events", type_="check")
    op.drop_constraint("ck_audit_event_scope_ids", "audit_events", type_="check")
    op.drop_constraint("ck_audit_event_scope", "audit_events", type_="check")
    op.drop_constraint(
        "ck_audit_authorization_scope", "audit_events", type_="check",
    )
    # The 20260804_09 API only understands the legacy audit enums.
    op.execute(sa.text("""
        UPDATE audit_events
        SET category = CASE
                WHEN category = 'security' THEN 'management'
                ELSE category
            END,
            source = CASE
                WHEN source = 'auth' THEN 'system'
                ELSE source
            END
        WHERE category = 'security' OR source = 'auth'
    """))

    op.add_column(
        "audit_events",
        sa.Column(
            "actor_role",
            sa.String(40),
            nullable=True,
            server_default="unknown",
        ),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "actor_role",
            sa.String(40),
            nullable=True,
            server_default="unknown",
        ),
    )
    op.execute(sa.text(
        "UPDATE audit_events SET actor_role = "
        f"{_legacy_role_snapshot('actor_roles_json')}"
    ))
    op.execute(sa.text(
        "UPDATE agent_runs SET actor_role = "
        f"{_legacy_role_snapshot('actor_roles_json')}"
    ))
    op.alter_column(
        "audit_events", "actor_role", nullable=False, server_default=None,
    )
    op.alter_column(
        "agent_runs", "actor_role", nullable=False, server_default=None,
    )
    op.drop_column("agent_runs", "actor_roles_json")
    op.drop_column("audit_events", "auth_method")
    op.drop_column("audit_events", "event_scope")
    op.drop_column("audit_events", "authorization_scope")
    op.drop_column("audit_events", "actor_roles_json")

    op.execute(sa.text(
        "UPDATE audit_events SET unit_id = 'legacy-unit' WHERE unit_id IS NULL"
    ))
    op.alter_column(
        "audit_events",
        "unit_id",
        existing_type=sa.String(64),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_audit_actor_role",
        "audit_events",
        "actor_role IN ("
        "'unknown','user','project_admin','unit_auditor',"
        "'project_admin,user','project_admin,unit_auditor',"
        "'unit_auditor,user','project_admin,unit_auditor,user'"
        ")",
    )
