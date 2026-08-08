import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


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


def new_id() -> str:
    return str(uuid.uuid4())


def _sql_values(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    __table_args__ = (
        Index(
            "uq_users_email_ci",
            func.lower(email),
            unique=True,
            postgresql_where=email.is_not(None),
            sqlite_where=email.is_not(None),
        ),
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    authorization_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ExternalIdentity(Base):
    __tablename__ = "external_identities"
    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_external_identity_issuer_subject"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    claims: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    last_login_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class ExternalIdentityHistory(Base):
    __tablename__ = "external_identity_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    changed_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class LocalCredential(Base):
    __tablename__ = "local_credentials"
    __table_args__ = (
        CheckConstraint("failed_attempts >= 0", name="ck_local_credentials_failed_attempts"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Unit(Base):
    __tablename__ = "units"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("id", "unit_id", name="uq_projects_id_unit"),
        UniqueConstraint("unit_id", "code", name="uq_projects_unit_code"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    unit_id: Mapped[str] = mapped_column(ForeignKey("units.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class UnitMembership(Base):
    __tablename__ = "unit_memberships"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','inactive')",
            name="ck_unit_memberships_status",
        ),
        UniqueConstraint("user_id", "unit_id", name="uq_unit_memberships_user_unit"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    unit_id: Mapped[str] = mapped_column(ForeignKey("units.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class ProjectMembership(Base):
    __tablename__ = "project_memberships"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','inactive')",
            name="ck_project_memberships_status",
        ),
        UniqueConstraint(
            "user_id",
            "unit_id",
            "project_id",
            name="uq_project_memberships_user_unit_project",
        ),
        ForeignKeyConstraint(
            ["user_id", "unit_id"],
            ["unit_memberships.user_id", "unit_memberships.unit_id"],
            name="fk_project_memberships_unit_member",
        ),
        ForeignKeyConstraint(
            ["project_id", "unit_id"],
            ["projects.id", "projects.unit_id"],
            name="fk_project_memberships_project_unit",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    unit_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (
        CheckConstraint(
            "(scope_type = 'platform' AND unit_id IS NULL) OR "
            "(scope_type IN ('unit','project') AND unit_id IS NOT NULL)",
            name="ck_roles_scope",
        ),
        UniqueConstraint("id", "scope_type", "unit_id", name="uq_roles_id_scope_unit"),
        UniqueConstraint("id", "unit_id", name="uq_roles_id_unit"),
        UniqueConstraint("unit_id", "code", name="uq_roles_unit_code"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    unit_id: Mapped[str | None] = mapped_column(ForeignKey("units.id"))
    built_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    resource: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (
        CheckConstraint(
            "data_scope IN ('unit','assigned_projects','project','own','custom_projects')",
            name="ck_role_permissions_data_scope",
        ),
        UniqueConstraint("id", "unit_id", name="uq_role_permissions_id_unit"),
        UniqueConstraint(
            "role_id",
            "permission_code",
            "data_scope",
            name="uq_role_permissions_grant",
        ),
        ForeignKeyConstraint(
            ["role_id", "unit_id"],
            ["roles.id", "roles.unit_id"],
            name="fk_role_permissions_role_unit",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    role_id: Mapped[str] = mapped_column(String(36), nullable=False)
    permission_code: Mapped[str] = mapped_column(
        ForeignKey("permissions.code"),
        nullable=False,
    )
    unit_id: Mapped[str] = mapped_column(ForeignKey("units.id"), nullable=False)
    data_scope: Mapped[str] = mapped_column(String(24), nullable=False)


class UnitMembershipRole(Base):
    __tablename__ = "unit_membership_roles"
    __table_args__ = (
        CheckConstraint("scope_type = 'unit'", name="ck_unit_membership_roles_scope"),
        UniqueConstraint(
            "user_id",
            "unit_id",
            "role_id",
            name="uq_unit_membership_roles_binding",
        ),
        ForeignKeyConstraint(
            ["user_id", "unit_id"],
            ["unit_memberships.user_id", "unit_memberships.unit_id"],
            name="fk_unit_membership_roles_member",
        ),
        ForeignKeyConstraint(
            ["role_id", "scope_type", "unit_id"],
            ["roles.id", "roles.scope_type", "roles.unit_id"],
            name="fk_unit_membership_roles_role",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    unit_id: Mapped[str] = mapped_column(String(36), nullable=False)
    role_id: Mapped[str] = mapped_column(String(36), nullable=False)
    scope_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="unit",
    )


class ProjectMembershipRole(Base):
    __tablename__ = "project_membership_roles"
    __table_args__ = (
        CheckConstraint(
            "scope_type = 'project'",
            name="ck_project_membership_roles_scope",
        ),
        UniqueConstraint(
            "user_id",
            "unit_id",
            "project_id",
            "role_id",
            name="uq_project_membership_roles_binding",
        ),
        ForeignKeyConstraint(
            ["user_id", "unit_id", "project_id"],
            [
                "project_memberships.user_id",
                "project_memberships.unit_id",
                "project_memberships.project_id",
            ],
            name="fk_project_membership_roles_member",
        ),
        ForeignKeyConstraint(
            ["role_id", "scope_type", "unit_id"],
            ["roles.id", "roles.scope_type", "roles.unit_id"],
            name="fk_project_membership_roles_role",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    unit_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    role_id: Mapped[str] = mapped_column(String(36), nullable=False)
    scope_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="project",
    )


class RolePermissionProject(Base):
    __tablename__ = "role_permission_projects"
    __table_args__ = (
        UniqueConstraint(
            "role_permission_id",
            "project_id",
            name="uq_role_permission_projects_grant_project",
        ),
        ForeignKeyConstraint(
            ["role_permission_id", "unit_id"],
            ["role_permissions.id", "role_permissions.unit_id"],
            name="fk_role_permission_projects_grant_unit",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "unit_id"],
            ["projects.id", "projects.unit_id"],
            name="fk_role_permission_projects_project_unit",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    role_permission_id: Mapped[str] = mapped_column(String(36), nullable=False)
    unit_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)


class Menu(Base):
    __tablename__ = "menus"
    __table_args__ = (
        CheckConstraint("kind IN ('group','route')", name="ck_menus_kind"),
        CheckConstraint(
            "(kind = 'group' AND route_key IS NULL AND visibility_target IS NULL "
            "AND requires_current_project = false) OR "
            "(kind = 'route' AND route_key IS NOT NULL "
            "AND visibility_target IS NOT NULL AND "
            f"((route_key IN ({_sql_values(UNIT_ROUTE_KEYS)}) "
            "AND visibility_target = 'unit' AND "
            "requires_current_project = (route_key = 'chat')) OR "
            f"(route_key IN ({_sql_values(CURRENT_PROJECT_ROUTE_KEYS)}) "
            "AND visibility_target = 'current_project' "
            "AND requires_current_project = true)))",
            name="ck_menu_catalogue",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    node_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    route_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("menus.id"))
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    visibility_target: Mapped[str | None] = mapped_column(String(20))
    requires_current_project: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )


class MenuPermission(Base):
    __tablename__ = "menu_permissions"
    __table_args__ = (
        UniqueConstraint(
            "menu_id",
            "permission_code",
            name="uq_menu_permissions_mapping",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    menu_id: Mapped[str] = mapped_column(
        ForeignKey("menus.id", ondelete="CASCADE"),
        nullable=False,
    )
    permission_code: Mapped[str] = mapped_column(
        ForeignKey("permissions.code"),
        nullable=False,
    )


class OidcLoginTransaction(Base):
    __tablename__ = "oidc_login_transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    nonce_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    browser_correlation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    pkce_verifier_encrypted: Mapped[dict[str, str]] = mapped_column(
        JSON,
        nullable=False,
    )
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    return_to: Mapped[str] = mapped_column(String(1024), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint(
            "auth_method IN ('oidc','dev_test','local')",
            name="ck_auth_sessions_method",
        ),
        ForeignKeyConstraint(
            ["current_project_id", "unit_id"],
            ["projects.id", "projects.unit_id"],
            name="fk_auth_sessions_current_project_unit",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_token_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    unit_id: Mapped[str] = mapped_column(ForeignKey("units.id"), nullable=False)
    current_project_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    auth_method: Mapped[str] = mapped_column(String(20), nullable=False)
    csrf_secret_encrypted: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    provider_tokens_encrypted: Mapped[dict[str, str] | None] = mapped_column(JSON)
    provider_sid: Mapped[str | None] = mapped_column(String(255))
    authorization_version: Mapped[int] = mapped_column(Integer, nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    absolute_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


def validate_role_permission_project(
    role_permission: RolePermission,
    project: Project,
) -> None:
    if role_permission.data_scope != "custom_projects":
        raise ValueError("role permission data_scope must be custom_projects")
    if role_permission.unit_id != project.unit_id:
        raise ValueError("role permission and project must belong to the same unit")
