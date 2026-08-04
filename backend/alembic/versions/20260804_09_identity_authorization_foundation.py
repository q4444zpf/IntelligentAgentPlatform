"""add identity and authorization foundation

Revision ID: 20260804_09
Revises: 20260804_08
Create Date: 2026-08-04
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260804_09"
down_revision: str | None = "20260804_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNIT_ROUTE_KEYS = (
    "dashboard",
    "chat",
    "agent-manage",
    "llm",
    "mcp",
    "skill",
    "tools",
    "external-agents",
    "unit-resources",
    "sandbox",
    "policies",
    "credentials",
    "audit",
    "users",
    "unit-projects",
    "roles",
    "integration",
    "settings",
)

CURRENT_PROJECT_ROUTE_KEYS = (
    "collaboration",
    "workflow",
    "knowledge",
    "prompt",
    "my-agents",
    "my-mcp",
    "my-skills",
    "my-publish",
    "project-resources",
    "hydraulic-topology",
    "public-agents",
    "public-mcp",
    "public-skills",
    "publish-review",
    "runs",
    "async-tasks",
    "artifacts",
    "approvals",
)


def _sql_values(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


MENU_CATALOGUE_CHECK = (
    "(kind = 'group' AND route_key IS NULL AND visibility_target IS NULL "
    "AND requires_current_project = false) OR "
    f"(kind = 'route' AND ((route_key IN ({_sql_values(UNIT_ROUTE_KEYS)}) "
    "AND visibility_target = 'unit' AND "
    "requires_current_project = (route_key = 'chat')) OR "
    f"(route_key IN ({_sql_values(CURRENT_PROJECT_ROUTE_KEYS)}) "
    "AND visibility_target = 'current_project' "
    "AND requires_current_project = true)))"
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("authorization_version", sa.Integer(), nullable=False),
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
    )
    op.create_table(
        "units",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("code", name="uq_units_code"),
    )
    op.create_table(
        "permissions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("resource", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("risk_level", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.UniqueConstraint("code", name="uq_permissions_code"),
    )
    op.create_table(
        "external_identities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("issuer", sa.String(512), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("claims", sa.JSON(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "issuer",
            "subject",
            name="uq_external_identity_issuer_subject",
        ),
    )
    op.create_table(
        "external_identity_history",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("issuer", sa.String(512), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("changed_by_user_id", sa.String(36), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["changed_by_user_id"], ["users.id"]),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("unit_id", sa.String(36), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"]),
        sa.UniqueConstraint("id", "unit_id", name="uq_projects_id_unit"),
        sa.UniqueConstraint("unit_id", "code", name="uq_projects_unit_code"),
    )
    op.create_table(
        "unit_memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("unit_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active','inactive')",
            name="ck_unit_memberships_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"]),
        sa.UniqueConstraint(
            "user_id",
            "unit_id",
            name="uq_unit_memberships_user_unit",
        ),
    )
    op.create_table(
        "roles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("scope_type", sa.String(20), nullable=False),
        sa.Column("unit_id", sa.String(36), nullable=True),
        sa.Column("built_in", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(scope_type = 'platform' AND unit_id IS NULL) OR "
            "(scope_type IN ('unit','project') AND unit_id IS NOT NULL)",
            name="ck_roles_scope",
        ),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"]),
        sa.UniqueConstraint("id", "scope_type", "unit_id", name="uq_roles_id_scope_unit"),
        sa.UniqueConstraint("id", "unit_id", name="uq_roles_id_unit"),
        sa.UniqueConstraint("unit_id", "code", name="uq_roles_unit_code"),
    )
    op.create_table(
        "menus",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("node_key", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("route_key", sa.String(64), nullable=True),
        sa.Column("parent_id", sa.String(36), nullable=True),
        sa.Column("title", sa.String(80), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("visibility_target", sa.String(20), nullable=True),
        sa.Column("requires_current_project", sa.Boolean(), nullable=False),
        sa.CheckConstraint("kind IN ('group','route')", name="ck_menus_kind"),
        sa.CheckConstraint(MENU_CATALOGUE_CHECK, name="ck_menu_catalogue"),
        sa.ForeignKeyConstraint(["parent_id"], ["menus.id"]),
        sa.UniqueConstraint("node_key", name="uq_menus_node_key"),
        sa.UniqueConstraint("route_key", name="uq_menus_route_key"),
    )
    op.create_table(
        "oidc_login_transactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("nonce_hash", sa.String(64), nullable=False),
        sa.Column("browser_correlation_hash", sa.String(64), nullable=False),
        sa.Column("pkce_verifier_encrypted", sa.JSON(), nullable=False),
        sa.Column("issuer", sa.String(512), nullable=False),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("redirect_uri", sa.String(2048), nullable=False),
        sa.Column("return_to", sa.String(1024), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("state_hash", name="uq_oidc_login_transactions_state_hash"),
        sa.UniqueConstraint("nonce_hash", name="uq_oidc_login_transactions_nonce_hash"),
    )
    op.create_table(
        "project_memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("unit_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active','inactive')",
            name="ck_project_memberships_status",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "unit_id"],
            ["unit_memberships.user_id", "unit_memberships.unit_id"],
            name="fk_project_memberships_unit_member",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "unit_id"],
            ["projects.id", "projects.unit_id"],
            name="fk_project_memberships_project_unit",
        ),
        sa.UniqueConstraint(
            "user_id",
            "unit_id",
            "project_id",
            name="uq_project_memberships_user_unit_project",
        ),
    )
    op.create_table(
        "role_permissions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("role_id", sa.String(36), nullable=False),
        sa.Column("permission_code", sa.String(100), nullable=False),
        sa.Column("unit_id", sa.String(36), nullable=False),
        sa.Column("data_scope", sa.String(24), nullable=False),
        sa.CheckConstraint(
            "data_scope IN ('unit','assigned_projects','project','own','custom_projects')",
            name="ck_role_permissions_data_scope",
        ),
        sa.ForeignKeyConstraint(
            ["role_id", "unit_id"],
            ["roles.id", "roles.unit_id"],
            name="fk_role_permissions_role_unit",
        ),
        sa.ForeignKeyConstraint(["permission_code"], ["permissions.code"]),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"]),
        sa.UniqueConstraint("id", "unit_id", name="uq_role_permissions_id_unit"),
        sa.UniqueConstraint(
            "role_id",
            "permission_code",
            "data_scope",
            name="uq_role_permissions_grant",
        ),
    )
    op.create_table(
        "unit_membership_roles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("unit_id", sa.String(36), nullable=False),
        sa.Column("role_id", sa.String(36), nullable=False),
        sa.Column("scope_type", sa.String(20), nullable=False),
        sa.CheckConstraint("scope_type = 'unit'", name="ck_unit_membership_roles_scope"),
        sa.ForeignKeyConstraint(
            ["user_id", "unit_id"],
            ["unit_memberships.user_id", "unit_memberships.unit_id"],
            name="fk_unit_membership_roles_member",
        ),
        sa.ForeignKeyConstraint(
            ["role_id", "scope_type", "unit_id"],
            ["roles.id", "roles.scope_type", "roles.unit_id"],
            name="fk_unit_membership_roles_role",
        ),
        sa.UniqueConstraint(
            "user_id",
            "unit_id",
            "role_id",
            name="uq_unit_membership_roles_binding",
        ),
    )
    op.create_table(
        "project_membership_roles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("unit_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("role_id", sa.String(36), nullable=False),
        sa.Column("scope_type", sa.String(20), nullable=False),
        sa.CheckConstraint(
            "scope_type = 'project'",
            name="ck_project_membership_roles_scope",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "unit_id", "project_id"],
            [
                "project_memberships.user_id",
                "project_memberships.unit_id",
                "project_memberships.project_id",
            ],
            name="fk_project_membership_roles_member",
        ),
        sa.ForeignKeyConstraint(
            ["role_id", "scope_type", "unit_id"],
            ["roles.id", "roles.scope_type", "roles.unit_id"],
            name="fk_project_membership_roles_role",
        ),
        sa.UniqueConstraint(
            "user_id",
            "unit_id",
            "project_id",
            "role_id",
            name="uq_project_membership_roles_binding",
        ),
    )
    op.create_table(
        "role_permission_projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("role_permission_id", sa.String(36), nullable=False),
        sa.Column("unit_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(
            ["role_permission_id", "unit_id"],
            ["role_permissions.id", "role_permissions.unit_id"],
            name="fk_role_permission_projects_grant_unit",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "unit_id"],
            ["projects.id", "projects.unit_id"],
            name="fk_role_permission_projects_project_unit",
        ),
        sa.UniqueConstraint(
            "role_permission_id",
            "project_id",
            name="uq_role_permission_projects_grant_project",
        ),
    )
    op.create_table(
        "menu_permissions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("menu_id", sa.String(36), nullable=False),
        sa.Column("permission_code", sa.String(100), nullable=False),
        sa.ForeignKeyConstraint(["menu_id"], ["menus.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["permission_code"], ["permissions.code"]),
        sa.UniqueConstraint(
            "menu_id",
            "permission_code",
            name="uq_menu_permissions_mapping",
        ),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_token_hash", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("unit_id", sa.String(36), nullable=False),
        sa.Column("current_project_id", sa.String(36), nullable=True),
        sa.Column("auth_method", sa.String(20), nullable=False),
        sa.Column("csrf_secret_encrypted", sa.JSON(), nullable=False),
        sa.Column("provider_tokens_encrypted", sa.JSON(), nullable=True),
        sa.Column("provider_sid", sa.String(255), nullable=True),
        sa.Column("authorization_version", sa.Integer(), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "auth_method IN ('oidc','dev_test')",
            name="ck_auth_sessions_method",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"]),
        sa.ForeignKeyConstraint(
            ["current_project_id", "unit_id"],
            ["projects.id", "projects.unit_id"],
            name="fk_auth_sessions_current_project_unit",
        ),
        sa.UniqueConstraint(
            "session_token_hash",
            name="uq_auth_sessions_token_hash",
        ),
    )

    op.execute(sa.text("""
        CREATE FUNCTION enforce_role_permission_project_custom_scope()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM role_permissions
                WHERE id = NEW.role_permission_id
                  AND unit_id = NEW.unit_id
                  AND data_scope = 'custom_projects'
            ) THEN
                RAISE EXCEPTION
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_role_permission_projects_custom_scope',
                          MESSAGE = 'role permission project requires custom_projects data scope';
            END IF;
            RETURN NEW;
        END;
        $$
    """))
    op.execute(sa.text("""
        CREATE CONSTRAINT TRIGGER ck_role_permission_projects_custom_scope
        AFTER INSERT OR UPDATE ON role_permission_projects
        DEFERRABLE INITIALLY IMMEDIATE
        FOR EACH ROW
        EXECUTE FUNCTION enforce_role_permission_project_custom_scope()
    """))
    op.execute(sa.text("""
        CREATE FUNCTION enforce_role_permission_custom_scope_dependents()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.data_scope <> 'custom_projects'
               AND EXISTS (
                    SELECT 1
                    FROM role_permission_projects
                    WHERE role_permission_id = NEW.id
                      AND unit_id = NEW.unit_id
               ) THEN
                RAISE EXCEPTION
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_role_permission_projects_custom_scope',
                          MESSAGE = 'custom project mappings require custom_projects data scope';
            END IF;
            RETURN NEW;
        END;
        $$
    """))
    op.execute(sa.text("""
        CREATE CONSTRAINT TRIGGER ck_role_permissions_custom_scope_dependents
        AFTER UPDATE ON role_permissions
        DEFERRABLE INITIALLY IMMEDIATE
        FOR EACH ROW
        EXECUTE FUNCTION enforce_role_permission_custom_scope_dependents()
    """))


def downgrade() -> None:
    op.execute(sa.text(
        "DROP TRIGGER IF EXISTS ck_role_permissions_custom_scope_dependents "
        "ON role_permissions"
    ))
    op.execute(sa.text(
        "DROP FUNCTION IF EXISTS enforce_role_permission_custom_scope_dependents()"
    ))
    op.execute(sa.text(
        "DROP TRIGGER IF EXISTS ck_role_permission_projects_custom_scope "
        "ON role_permission_projects"
    ))
    op.execute(sa.text(
        "DROP FUNCTION IF EXISTS enforce_role_permission_project_custom_scope()"
    ))
    op.drop_table("auth_sessions")
    op.drop_table("menu_permissions")
    op.drop_table("role_permission_projects")
    op.drop_table("project_membership_roles")
    op.drop_table("unit_membership_roles")
    op.drop_table("role_permissions")
    op.drop_table("project_memberships")
    op.drop_table("oidc_login_transactions")
    op.drop_table("menus")
    op.drop_table("roles")
    op.drop_table("unit_memberships")
    op.drop_table("projects")
    op.drop_table("external_identity_history")
    op.drop_table("external_identities")
    op.drop_table("permissions")
    op.drop_table("units")
    op.drop_table("users")
