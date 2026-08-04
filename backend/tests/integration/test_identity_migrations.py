from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="requires PostgreSQL",
)

ALEMBIC = (sys.executable, "-m", "alembic", "-c", "backend/alembic.ini")
IDENTITY_TABLES = {
    "users",
    "external_identities",
    "external_identity_history",
    "units",
    "projects",
    "unit_memberships",
    "project_memberships",
    "roles",
    "permissions",
    "role_permissions",
    "unit_membership_roles",
    "project_membership_roles",
    "role_permission_projects",
    "menus",
    "menu_permissions",
    "oidc_login_transactions",
    "auth_sessions",
}


def _alembic(*arguments: str) -> None:
    env = os.environ | {"DATABASE_URL": os.environ["TEST_DATABASE_URL"]}
    subprocess.run((*ALEMBIC, *arguments), check=True, env=env)


def _load_models() -> None:
    global AuthSession
    global ExternalIdentity
    global Menu
    global OidcLoginTransaction
    global Permission
    global Project
    global ProjectMembership
    global Role
    global RolePermission
    global RolePermissionProject
    global Unit
    global UnitMembership
    global UnitMembershipRole
    global User

    from app.identity.models import (
        AuthSession,
        ExternalIdentity,
        Menu,
        OidcLoginTransaction,
        Permission,
        Project,
        ProjectMembership,
        Role,
        RolePermission,
        RolePermissionProject,
        Unit,
        UnitMembership,
        UnitMembershipRole,
        User,
    )


@pytest.fixture(scope="module")
def postgres_engine():
    _alembic("upgrade", "20260804_08")
    _alembic("upgrade", "20260804_09")
    _load_models()
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    yield engine
    engine.dispose()


@pytest.fixture
def postgres_session(postgres_engine):
    session = Session(postgres_engine)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def seed_user(session: Session, user_id: str = "u1") -> None:
    session.add(User(
        id=user_id,
        display_name="Test User",
        email=None,
        status="active",
        authorization_version=1,
    ))
    session.flush()


def seed_unit(session: Session, unit_id: str) -> None:
    session.add(Unit(id=unit_id, code=unit_id, name=unit_id, status="active"))
    session.flush()


def seed_unit_member(session: Session, user_id: str, unit_id: str) -> None:
    seed_user(session, user_id)
    seed_unit(session, unit_id)
    session.add(UnitMembership(
        id=f"um-{user_id}-{unit_id}",
        user_id=user_id,
        unit_id=unit_id,
        status="active",
    ))
    session.flush()


def seed_project(session: Session, project_id: str, unit_id: str) -> None:
    session.add(Project(
        id=project_id,
        unit_id=unit_id,
        code=project_id,
        name=project_id,
        status="active",
    ))
    session.flush()


def auth_session(session_id: str, token_hash: str) -> AuthSession:
    now = datetime.now(UTC)
    return AuthSession(
        id=session_id,
        session_token_hash=token_hash,
        user_id="u1",
        unit_id="unit-a",
        current_project_id=None,
        auth_method="oidc",
        csrf_secret_encrypted={"ciphertext": "test"},
        provider_tokens_encrypted=None,
        provider_sid=None,
        authorization_version=1,
        idle_expires_at=now + timedelta(minutes=30),
        absolute_expires_at=now + timedelta(hours=8),
        last_seen_at=now,
        revoked_at=None,
        revoke_reason=None,
    )


def login_transaction(
    transaction_id: str,
    state_hash: str,
    *,
    expires_at: datetime,
    consumed_at: datetime | None,
) -> OidcLoginTransaction:
    return OidcLoginTransaction(
        id=transaction_id,
        state_hash=state_hash,
        nonce_hash=f"nonce-{transaction_id}",
        browser_correlation_hash=f"browser-{transaction_id}",
        pkce_verifier_encrypted={"ciphertext": "test"},
        issuer="https://issuer.example",
        client_id="client-id",
        redirect_uri="https://app.example/api/auth/callback",
        return_to="/dashboard",
        expires_at=expires_at,
        consumed_at=consumed_at,
    )


def test_identity_revision_upgrades_downgrades_and_reupgrades(postgres_engine):
    inspector = inspect(postgres_engine)
    assert IDENTITY_TABLES <= set(inspector.get_table_names())
    assert "ck_menu_catalogue" in {
        constraint["name"] for constraint in inspector.get_check_constraints("menus")
    }
    with postgres_engine.connect() as connection:
        assert "ck_role_permission_projects_custom_scope" in {
            trigger[0]
            for trigger in connection.exec_driver_sql("""
                SELECT tgname
                FROM pg_trigger
                WHERE tgrelid = 'role_permission_projects'::regclass
                  AND NOT tgisinternal
            """).all()
        }

    postgres_engine.dispose()
    _alembic("downgrade", "20260804_08")
    downgraded_engine = create_engine(os.environ["TEST_DATABASE_URL"])
    try:
        downgraded_tables = set(inspect(downgraded_engine).get_table_names())
        assert not (IDENTITY_TABLES & downgraded_tables)
        assert "audit_events" in downgraded_tables
    finally:
        downgraded_engine.dispose()

    _alembic("upgrade", "20260804_09")
    reupgraded_engine = create_engine(os.environ["TEST_DATABASE_URL"])
    try:
        assert IDENTITY_TABLES <= set(inspect(reupgraded_engine).get_table_names())
    finally:
        reupgraded_engine.dispose()


def test_project_membership_cannot_cross_unit(postgres_session):
    seed_unit_member(postgres_session, user_id="u1", unit_id="unit-a")
    seed_unit(postgres_session, "unit-b")
    seed_project(postgres_session, project_id="p1", unit_id="unit-b")
    postgres_session.add(ProjectMembership(
        id="pm1",
        user_id="u1",
        unit_id="unit-a",
        project_id="p1",
        status="active",
    ))
    with pytest.raises(IntegrityError):
        postgres_session.flush()


def test_project_role_cannot_bind_to_unit_membership(postgres_session):
    seed_unit_member(postgres_session, user_id="u1", unit_id="unit-a")
    postgres_session.add(Role(
        id="project-role",
        code="project-role",
        name="Project Role",
        scope_type="project",
        unit_id="unit-a",
        built_in=False,
        status="active",
    ))
    postgres_session.flush()
    postgres_session.add(UnitMembershipRole(
        id="binding-1",
        user_id="u1",
        unit_id="unit-a",
        role_id="project-role",
        scope_type="unit",
    ))
    with pytest.raises(IntegrityError):
        postgres_session.flush()


def test_custom_projects_cannot_reference_another_unit(postgres_session):
    seed_unit_member(postgres_session, user_id="u1", unit_id="unit-a")
    seed_unit(postgres_session, "unit-b")
    seed_project(postgres_session, project_id="p-b", unit_id="unit-b")
    postgres_session.add_all([
        Role(
            id="role-a",
            code="role-a",
            name="Role A",
            scope_type="unit",
            unit_id="unit-a",
            built_in=False,
            status="active",
        ),
        Permission(
            id="permission-1",
            code="project.read",
            resource="project",
            action="read",
            risk_level="low",
            status="active",
        ),
    ])
    postgres_session.flush()
    postgres_session.add(RolePermission(
        id="grant-a",
        role_id="role-a",
        permission_code="project.read",
        unit_id="unit-a",
        data_scope="custom_projects",
    ))
    postgres_session.flush()
    postgres_session.add(RolePermissionProject(
        id="grant-project-1",
        role_permission_id="grant-a",
        unit_id="unit-b",
        project_id="p-b",
    ))
    with pytest.raises(IntegrityError):
        postgres_session.flush()


def test_non_custom_grant_cannot_have_custom_projects(postgres_session):
    seed_unit_member(postgres_session, user_id="u1", unit_id="unit-a")
    seed_project(postgres_session, project_id="p-a", unit_id="unit-a")
    postgres_session.add_all([
        Role(
            id="role-a",
            code="role-a",
            name="Role A",
            scope_type="unit",
            unit_id="unit-a",
            built_in=False,
            status="active",
        ),
        Permission(
            id="permission-1",
            code="project.read",
            resource="project",
            action="read",
            risk_level="low",
            status="active",
        ),
    ])
    postgres_session.flush()
    postgres_session.add(RolePermission(
        id="grant-a",
        role_id="role-a",
        permission_code="project.read",
        unit_id="unit-a",
        data_scope="unit",
    ))
    postgres_session.flush()
    postgres_session.add(RolePermissionProject(
        id="grant-project-1",
        role_permission_id="grant-a",
        unit_id="unit-a",
        project_id="p-a",
    ))
    with pytest.raises(IntegrityError):
        postgres_session.flush()


def test_duplicate_external_identity_is_rejected_without_case_normalization(
    postgres_session,
):
    seed_user(postgres_session, "u1")
    postgres_session.add(ExternalIdentity(
        id="identity-1",
        user_id="u1",
        issuer="https://Issuer.example",
        subject="Subject-1",
        claims={"name": "Test User"},
        last_login_at=datetime.now(UTC),
    ))
    postgres_session.flush()
    postgres_session.add(ExternalIdentity(
        id="identity-2",
        user_id="u1",
        issuer="https://Issuer.example",
        subject="Subject-1",
        claims={},
        last_login_at=datetime.now(UTC),
    ))
    with pytest.raises(IntegrityError):
        postgres_session.flush()


def test_external_identity_comparison_preserves_case(postgres_session):
    seed_user(postgres_session, "u1")
    postgres_session.add_all([
        ExternalIdentity(
            id="identity-1",
            user_id="u1",
            issuer="https://Issuer.example",
            subject="Subject-1",
            claims={},
            last_login_at=datetime.now(UTC),
        ),
        ExternalIdentity(
            id="identity-2",
            user_id="u1",
            issuer="https://issuer.example",
            subject="subject-1",
            claims={},
            last_login_at=datetime.now(UTC),
        ),
    ])
    postgres_session.flush()


def test_multiple_active_sessions_may_exist(postgres_session):
    seed_unit_member(postgres_session, user_id="u1", unit_id="unit-a")
    postgres_session.add_all([
        auth_session("session-1", "hash-1"),
        auth_session("session-2", "hash-2"),
    ])
    postgres_session.flush()
    assert postgres_session.query(AuthSession).count() == 2


def test_session_current_project_cannot_cross_unit(postgres_session):
    seed_unit_member(postgres_session, user_id="u1", unit_id="unit-a")
    seed_unit(postgres_session, "unit-b")
    seed_project(postgres_session, project_id="p-b", unit_id="unit-b")
    record = auth_session("session-1", "hash-1")
    record.current_project_id = "p-b"
    postgres_session.add(record)
    with pytest.raises(IntegrityError):
        postgres_session.flush()


def test_menu_catalogue_accepts_all_exact_route_targets(postgres_session):
    unit_routes = (
        "dashboard", "chat", "agent-manage", "llm", "mcp", "skill",
        "tools", "external-agents", "unit-resources", "sandbox", "policies",
        "credentials", "audit", "users", "unit-projects", "roles",
        "integration", "settings",
    )
    project_routes = (
        "collaboration", "workflow", "knowledge", "prompt", "my-agents",
        "my-mcp", "my-skills", "my-publish", "project-resources",
        "hydraulic-topology", "public-agents", "public-mcp", "public-skills",
        "publish-review", "runs", "async-tasks", "artifacts", "approvals",
    )
    postgres_session.add(Menu(
        id="group-1",
        node_key="group-1",
        kind="group",
        route_key=None,
        parent_id=None,
        title="Group",
        sort_order=0,
        status="active",
        visibility_target=None,
        requires_current_project=False,
    ))
    for sort_order, route_key in enumerate(unit_routes + project_routes, start=1):
        target = "unit" if route_key in unit_routes else "current_project"
        postgres_session.add(Menu(
            id=f"route-{sort_order}",
            node_key=f"node-{route_key}",
            kind="route",
            route_key=route_key,
            parent_id="group-1",
            title=route_key,
            sort_order=sort_order,
            status="active",
            visibility_target=target,
            requires_current_project=(target == "current_project" or route_key == "chat"),
        ))
    postgres_session.flush()


@pytest.mark.parametrize(
    ("kind", "route_key", "visibility_target", "requires_project"),
    [
        ("group", "dashboard", None, False),
        ("route", "unknown-route", "unit", False),
        ("route", "dashboard", "current_project", True),
        ("route", "collaboration", "unit", False),
        ("route", "chat", "unit", False),
        ("route", "dashboard", "unit", True),
        ("route", "collaboration", "current_project", False),
    ],
)
def test_menu_catalogue_rejects_shape_target_and_project_requirement_drift(
    postgres_session,
    kind,
    route_key,
    visibility_target,
    requires_project,
):
    postgres_session.add(Menu(
        id="invalid-menu",
        node_key="invalid-menu",
        kind=kind,
        route_key=route_key,
        parent_id=None,
        title="Invalid",
        sort_order=0,
        status="active",
        visibility_target=visibility_target,
        requires_current_project=requires_project,
    ))
    with pytest.raises(IntegrityError):
        postgres_session.flush()


@pytest.mark.parametrize("lifecycle", ["consumed", "expired"])
def test_login_transactions_remain_queryable_but_cannot_be_reused(
    postgres_session,
    lifecycle,
):
    now = datetime.now(UTC)
    consumed_at = now if lifecycle == "consumed" else None
    expires_at = now + timedelta(minutes=5) if lifecycle == "consumed" else now - timedelta(seconds=1)
    original = login_transaction(
        f"transaction-{lifecycle}",
        f"state-{lifecycle}",
        expires_at=expires_at,
        consumed_at=consumed_at,
    )
    postgres_session.add(original)
    postgres_session.flush()
    assert postgres_session.get(OidcLoginTransaction, original.id) is original

    postgres_session.add(login_transaction(
        f"transaction-{lifecycle}-reuse",
        f"state-{lifecycle}",
        expires_at=now + timedelta(minutes=5),
        consumed_at=None,
    ))
    with pytest.raises(IntegrityError):
        postgres_session.flush()
