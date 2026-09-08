import os
import subprocess
import sys

import pytest
import sqlalchemy as sa
from app.identity.catalogue import seed_builtin_catalogue
from app.identity.models import Role, RolePermission, Unit
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="requires dedicated PostgreSQL migration database",
)

ALEMBIC = (
    sys.executable,
    "-m",
    "alembic",
    "-c",
    "backend/alembic.ini",
)

units = sa.table(
    "units",
    sa.column("id"),
    sa.column("code"),
    sa.column("name"),
    sa.column("status"),
)
permissions = sa.table(
    "permissions",
    sa.column("id"),
    sa.column("code"),
    sa.column("resource"),
    sa.column("action"),
    sa.column("risk_level"),
    sa.column("status"),
)
roles = sa.table(
    "roles",
    sa.column("id"),
    sa.column("unit_id"),
    sa.column("code"),
    sa.column("name"),
    sa.column("scope_type"),
    sa.column("built_in"),
    sa.column("status"),
)
grants = sa.table(
    "role_permissions",
    sa.column("id"),
    sa.column("unit_id"),
    sa.column("role_id"),
    sa.column("permission_code"),
    sa.column("data_scope"),
)
menus = sa.table(
    "menus",
    sa.column("id"),
    sa.column("node_key"),
    sa.column("kind"),
    sa.column("route_key"),
    sa.column("parent_id"),
    sa.column("title"),
    sa.column("sort_order"),
    sa.column("status"),
    sa.column("visibility_target"),
    sa.column("requires_current_project"),
)
menu_permissions = sa.table(
    "menu_permissions",
    sa.column("id"),
    sa.column("menu_id"),
    sa.column("permission_code"),
)


def _run_migration(*arguments: str) -> None:
    environment = os.environ | {"DATABASE_URL": os.environ["TEST_DATABASE_URL"]}
    subprocess.run((*ALEMBIC, *arguments), check=True, env=environment)


@pytest.fixture(autouse=True)
def empty_migration_database():
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        yield
    finally:
        engine.dispose()


def test_upgrade_adds_only_missing_builtin_project_admin_grants():
    _run_migration("upgrade", "20260908_26")
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    unit_rows = [
        {
            "id": "unit-active",
            "code": "unit-active",
            "name": "Active",
            "status": "active",
        },
        {
            "id": "unit-inactive",
            "code": "unit-inactive",
            "name": "Inactive",
            "status": "active",
        },
        {
            "id": "unit-custom",
            "code": "unit-custom",
            "name": "Custom",
            "status": "active",
        },
        {
            "id": "unit-existing",
            "code": "unit-existing",
            "name": "Existing",
            "status": "active",
        },
    ]
    role_rows = [
        {
            "id": "role-active",
            "unit_id": "unit-active",
            "code": "project_admin",
            "name": "Active Built-in",
            "scope_type": "project",
            "built_in": True,
            "status": "active",
        },
        {
            "id": "role-inactive",
            "unit_id": "unit-inactive",
            "code": "project_admin",
            "name": "Inactive Built-in",
            "scope_type": "project",
            "built_in": True,
            "status": "inactive",
        },
        {
            "id": "role-custom",
            "unit_id": "unit-custom",
            "code": "project_admin",
            "name": "Custom Same-code",
            "scope_type": "project",
            "built_in": False,
            "status": "active",
        },
        {
            "id": "role-existing",
            "unit_id": "unit-existing",
            "code": "project_admin",
            "name": "Existing Grant",
            "scope_type": "project",
            "built_in": True,
            "status": "active",
        },
    ]
    permission_rows = [
        {
            "id": "permission-skill-read",
            "code": "skill.read",
            "resource": "skill",
            "action": "read",
            "risk_level": "medium",
            "status": "active",
        },
        {
            "id": "permission-skill-manage",
            "code": "skill.manage",
            "resource": "skill",
            "action": "manage",
            "risk_level": "medium",
            "status": "active",
        },
    ]
    grant_rows = [
        {
            "id": "grant-extra",
            "unit_id": "unit-active",
            "role_id": "role-active",
            "permission_code": "skill.read",
            "data_scope": "project",
        },
        {
            "id": "grant-existing-manage",
            "unit_id": "unit-existing",
            "role_id": "role-existing",
            "permission_code": "skill.manage",
            "data_scope": "project",
        },
    ]
    menu_rows = [
        {
            "id": "menu-skill",
            "node_key": "skill",
            "kind": "route",
            "route_key": "skill",
            "parent_id": None,
            "title": "Skill Test",
            "sort_order": 0,
            "status": "inactive",
            "visibility_target": "unit",
            "requires_current_project": False,
        }
    ]
    menu_permission_rows = [
        {
            "id": "menu-permission-skill-read",
            "menu_id": "menu-skill",
            "permission_code": "skill.read",
        }
    ]

    with engine.begin() as connection:
        connection.execute(units.insert(), unit_rows)
        connection.execute(permissions.insert(), permission_rows)
        connection.execute(roles.insert(), role_rows)
        connection.execute(grants.insert(), grant_rows)
        connection.execute(menus.insert(), menu_rows)
        connection.execute(menu_permissions.insert(), menu_permission_rows)
        roles_before = set(connection.execute(sa.select(roles)).tuples())
        grants_before = set(
            connection.execute(
                sa.select(
                    grants.c.unit_id,
                    grants.c.role_id,
                    grants.c.permission_code,
                    grants.c.data_scope,
                )
            ).tuples()
        )
        menus_before = set(connection.execute(sa.select(menus)).tuples())
        menu_permissions_before = set(
            connection.execute(sa.select(menu_permissions)).tuples()
        )

    _run_migration("upgrade", "head")

    with engine.connect() as connection:
        roles_after = set(connection.execute(sa.select(roles)).tuples())
        grants_after = set(
            connection.execute(
                sa.select(
                    grants.c.unit_id,
                    grants.c.role_id,
                    grants.c.permission_code,
                    grants.c.data_scope,
                )
            ).tuples()
        )
        menus_after = set(connection.execute(sa.select(menus)).tuples())
        menu_permissions_after = set(
            connection.execute(sa.select(menu_permissions)).tuples()
        )
        version = connection.scalar(text("SELECT version_num FROM alembic_version"))

    expected_additions = {
        ("unit-active", "role-active", "skill.manage", "project"),
        ("unit-inactive", "role-inactive", "skill.manage", "project"),
    }
    assert grants_after - grants_before == expected_additions
    assert grants_before - grants_after == set()
    assert (
        sum(
            grant[1:] == ("role-existing", "skill.manage", "project")
            for grant in grants_after
        )
        == 1
    )
    assert not any(grant[1] == "role-custom" for grant in grants_after)
    assert roles_after == roles_before
    assert menus_after == menus_before
    assert menu_permissions_after == menu_permissions_before
    assert version == "20260908_27"
    engine.dispose()


def test_upgrade_fails_when_skill_manage_permission_is_missing():
    _run_migration("upgrade", "20260908_26")
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    with engine.begin() as connection:
        connection.execute(
            units.insert().values(
                id="unit-missing-permission",
                code="unit-missing-permission",
                name="Missing Permission",
                status="active",
            )
        )
        connection.execute(
            roles.insert().values(
                id="role-missing-permission",
                unit_id="unit-missing-permission",
                code="project_admin",
                name="Missing Permission",
                scope_type="project",
                built_in=True,
                status="active",
            )
        )

    with pytest.raises(subprocess.CalledProcessError):
        _run_migration("upgrade", "head")

    with engine.connect() as connection:
        version = connection.scalar(text("SELECT version_num FROM alembic_version"))
        grant_count = connection.scalar(sa.select(sa.func.count()).select_from(grants))
    assert version == "20260908_26"
    assert grant_count == 0
    engine.dispose()


def test_empty_database_upgrade_allows_bootstrap_with_new_grant():
    _run_migration("upgrade", "head")
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    with Session(engine) as session:
        assert session.scalar(select(Role.id)) is None
        unit = Unit(
            id="unit-bootstrap",
            code="unit-bootstrap",
            name="Bootstrap",
            status="active",
        )
        session.add(unit)
        session.flush()
        seed_builtin_catalogue(session, unit.id)
        session.commit()

        project_admin_id = session.scalar(
            select(Role.id).where(
                Role.unit_id == unit.id,
                Role.code == "project_admin",
            )
        )
        grants_for_role = set(
            session.execute(
                select(RolePermission.permission_code, RolePermission.data_scope).where(
                    RolePermission.role_id == project_admin_id
                )
            ).tuples()
        )
        version = session.scalar(text("SELECT version_num FROM alembic_version"))

    assert ("skill.manage", "project") in grants_for_role
    assert version == "20260908_27"
    engine.dispose()
