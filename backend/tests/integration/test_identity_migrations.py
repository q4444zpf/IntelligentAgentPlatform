from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, inspect, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="requires PostgreSQL",
)

ALEMBIC = (sys.executable, "-m", "alembic", "-c", "backend/alembic.ini")
IDENTITY_FOUNDATION_TABLES = {
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
    global LocalCredential
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
        LocalCredential,
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
    _alembic("downgrade", "base")
    _alembic("upgrade", "20260804_08")
    _alembic("upgrade", "20260804_09")
    _load_models()
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    try:
        yield engine
    finally:
        engine.dispose()
        _alembic("upgrade", "head")


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


def seed_role_permission_project_scope(
    session: Session,
    *,
    prefix: str,
    data_scope: str = "custom_projects",
) -> tuple[RolePermission, Project]:
    user_id = f"{prefix}-user"
    unit_id = f"{prefix}-unit"
    project_id = f"{prefix}-project"
    role_id = f"{prefix}-role"
    permission_code = f"{prefix}.read"
    seed_unit_member(session, user_id=user_id, unit_id=unit_id)
    seed_project(session, project_id=project_id, unit_id=unit_id)
    session.add_all([
        Role(
            id=role_id,
            code=role_id,
            name=role_id,
            scope_type="unit",
            unit_id=unit_id,
            built_in=False,
            status="active",
        ),
        Permission(
            id=f"{prefix}-permission",
            code=permission_code,
            resource="project",
            action="read",
            risk_level="low",
            status="active",
        ),
    ])
    session.flush()
    grant = RolePermission(
        id=f"{prefix}-grant",
        role_id=role_id,
        permission_code=permission_code,
        unit_id=unit_id,
        data_scope=data_scope,
    )
    session.add(grant)
    session.flush()
    return grant, session.get(Project, project_id)


def consume_login_transaction(
    session: Session,
    *,
    state_hash: str,
    consumed_at: datetime,
) -> str | None:
    return session.execute(
        update(OidcLoginTransaction)
        .where(
            OidcLoginTransaction.state_hash == state_hash,
            OidcLoginTransaction.consumed_at.is_(None),
            OidcLoginTransaction.expires_at > consumed_at,
        )
        .values(consumed_at=consumed_at)
        .returning(OidcLoginTransaction.id)
    ).scalar_one_or_none()


def delete_role_permission_project_scope_fixture(connection, prefix: str) -> None:
    connection.execute(text(
        "DELETE FROM role_permission_projects WHERE id = :mapping_id"
    ), {"mapping_id": f"{prefix}-mapping"})
    connection.execute(text(
        "DELETE FROM role_permissions WHERE id = :grant_id"
    ), {"grant_id": f"{prefix}-grant"})
    connection.execute(text(
        "DELETE FROM permissions WHERE id = :permission_id"
    ), {"permission_id": f"{prefix}-permission"})
    connection.execute(text(
        "DELETE FROM roles WHERE id = :role_id"
    ), {"role_id": f"{prefix}-role"})
    connection.execute(text(
        "DELETE FROM projects WHERE id = :project_id"
    ), {"project_id": f"{prefix}-project"})
    connection.execute(text(
        "DELETE FROM unit_memberships WHERE id = :membership_id"
    ), {"membership_id": f"um-{prefix}-user-{prefix}-unit"})
    connection.execute(text(
        "DELETE FROM units WHERE id = :unit_id"
    ), {"unit_id": f"{prefix}-unit"})
    connection.execute(text(
        "DELETE FROM users WHERE id = :user_id"
    ), {"user_id": f"{prefix}-user"})


def test_identity_revision_upgrades_downgrades_and_reupgrades(postgres_engine):
    inspector = inspect(postgres_engine)
    assert IDENTITY_FOUNDATION_TABLES <= set(inspector.get_table_names())
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
        assert not (IDENTITY_FOUNDATION_TABLES & downgraded_tables)
        assert "audit_events" in downgraded_tables
    finally:
        downgraded_engine.dispose()

    _alembic("upgrade", "20260804_09")
    reupgraded_engine = create_engine(os.environ["TEST_DATABASE_URL"])
    try:
        assert IDENTITY_FOUNDATION_TABLES <= set(
            inspect(reupgraded_engine).get_table_names()
        )
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


def test_existing_custom_project_mapping_prevents_scope_change(postgres_engine):
    prefix = "rev"
    with Session(postgres_engine) as seed_session:
        grant, project = seed_role_permission_project_scope(
            seed_session,
            prefix=prefix,
        )
        seed_session.add(RolePermissionProject(
            id=f"{prefix}-mapping",
            role_permission_id=grant.id,
            unit_id=grant.unit_id,
            project_id=project.id,
        ))
        seed_session.commit()

    try:
        with Session(postgres_engine) as update_session:
            stored_grant = update_session.get(
                RolePermission,
                f"{prefix}-grant",
            )
            stored_grant.data_scope = "unit"
            with pytest.raises(IntegrityError):
                update_session.flush()
            update_session.rollback()

        with Session(postgres_engine) as verify_session:
            stored_grant = verify_session.get(
                RolePermission,
                f"{prefix}-grant",
            )
            stored_mapping = verify_session.get(
                RolePermissionProject,
                f"{prefix}-mapping",
            )
            assert stored_grant.data_scope == "custom_projects"
            assert stored_mapping is not None
    finally:
        with postgres_engine.begin() as connection:
            delete_role_permission_project_scope_fixture(connection, prefix)


def test_custom_project_insert_takes_parent_update_lock(
    postgres_engine,
):
    prefix = "conc"
    with Session(postgres_engine) as seed_session:
        seed_role_permission_project_scope(seed_session, prefix=prefix)
        seed_session.commit()

    updater = postgres_engine.connect()
    mapper = postgres_engine.connect()
    updater_transaction = updater.begin()
    mapper_transaction = mapper.begin()
    try:
        mapper.execute(text("""
            INSERT INTO role_permission_projects (
                id, role_permission_id, unit_id, project_id
            ) VALUES (
                :id, :grant_id, :unit_id, :project_id
            )
        """), {
            "id": f"{prefix}-mapping",
            "grant_id": f"{prefix}-grant",
            "unit_id": f"{prefix}-unit",
            "project_id": f"{prefix}-project",
        })
        updater.execute(text("SET LOCAL lock_timeout = '250ms'"))

        with pytest.raises(OperationalError) as lock_error:
            updater.execute(text(
                "SELECT id FROM role_permissions "
                "WHERE id = :grant_id FOR NO KEY UPDATE"
            ), {"grant_id": f"{prefix}-grant"}).all()
        assert lock_error.value.orig.sqlstate == "55P03"
        updater_transaction.rollback()
        mapper_transaction.commit()

        with Session(postgres_engine) as verify_session:
            stored_grant = verify_session.get(
                RolePermission,
                f"{prefix}-grant",
            )
            mapping_count = (
                verify_session.query(RolePermissionProject)
                .filter(
                    RolePermissionProject.role_permission_id
                    == f"{prefix}-grant"
                )
                .count()
            )
            assert stored_grant.data_scope == "custom_projects"
            assert mapping_count == 1
    finally:
        if mapper_transaction.is_active:
            mapper_transaction.rollback()
        if updater_transaction.is_active:
            updater_transaction.rollback()
        mapper.close()
        updater.close()
        with postgres_engine.begin() as connection:
            delete_role_permission_project_scope_fixture(connection, prefix)


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
        ("route", None, "unit", False),
        ("route", "dashboard", None, False),
        ("route", None, None, False),
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
def test_consumed_and_expired_login_transactions_are_auditable_not_claimable(
    postgres_engine,
    lifecycle,
):
    now = datetime.now(UTC)
    transaction_id = f"transaction-{lifecycle}"
    state_hash = f"state-{lifecycle}"
    consumed_at = now if lifecycle == "consumed" else None
    expires_at = (
        now + timedelta(minutes=5)
        if lifecycle == "consumed"
        else now - timedelta(seconds=1)
    )
    original = login_transaction(
        transaction_id,
        state_hash,
        expires_at=expires_at,
        consumed_at=consumed_at,
    )
    with Session(postgres_engine) as seed_session:
        seed_session.add(original)
        seed_session.commit()

    try:
        with Session(postgres_engine) as audit_session:
            audited = audit_session.execute(
                select(OidcLoginTransaction)
                .where(OidcLoginTransaction.id == transaction_id)
            ).scalar_one()
            assert audited is not original
            assert audited.id == transaction_id
            assert audited.expires_at == expires_at
            assert audited.consumed_at == consumed_at

            claimed_id = consume_login_transaction(
                audit_session,
                state_hash=state_hash,
                consumed_at=now,
            )
            audit_session.commit()
            assert claimed_id is None

        with Session(postgres_engine) as verify_session:
            retained = verify_session.get(OidcLoginTransaction, transaction_id)
            assert retained is not None
            assert retained.consumed_at == consumed_at
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text(
                "DELETE FROM oidc_login_transactions WHERE id = :id"
            ), {"id": transaction_id})


def test_login_transaction_atomic_claim_is_one_time(postgres_engine):
    now = datetime.now(UTC)
    transaction_id = "transaction-active"
    state_hash = "state-active"
    original = login_transaction(
        transaction_id,
        state_hash,
        expires_at=now + timedelta(minutes=5),
        consumed_at=None,
    )
    with Session(postgres_engine) as seed_session:
        seed_session.add(original)
        seed_session.commit()

    try:
        with Session(postgres_engine) as first_session:
            first_claim = consume_login_transaction(
                first_session,
                state_hash=state_hash,
                consumed_at=now,
            )
            first_session.commit()
            assert first_claim == transaction_id

        with Session(postgres_engine) as second_session:
            second_claim = consume_login_transaction(
                second_session,
                state_hash=state_hash,
                consumed_at=now + timedelta(seconds=1),
            )
            second_session.commit()
            assert second_claim is None

        with Session(postgres_engine) as audit_session:
            retained = audit_session.get(OidcLoginTransaction, transaction_id)
            assert retained is not None
            assert retained.consumed_at == now
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text(
                "DELETE FROM oidc_login_transactions WHERE id = :id"
            ), {"id": transaction_id})
