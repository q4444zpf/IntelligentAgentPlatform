from collections.abc import Iterator

import pytest
from app.core.request_context import RequestContext
from app.db.base import Base
from app.identity.models import (
    Project,
    ProjectMembership,
    Unit,
    UnitMembership,
    User,
)
from app.identity.schemas import AuthorizationContext, PermissionGrant
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def make_context(
    *permissions: str,
    user_id: str = "user-1",
    unit_id: str = "unit-1",
    project_id: str = "project-1",
    data_scope: str = "project",
) -> RequestContext:
    project_ids = (
        frozenset()
        if data_scope == "unit" or not project_id
        else frozenset({project_id})
    )
    authorization = AuthorizationContext(
        session_id="skill-test-session",
        user_id=user_id,
        unit_id=unit_id,
        current_project_id=project_id or None,
        auth_method="dev_test",
        authorization_version=1,
        role_codes=("viewer",),
        grants=tuple(
            PermissionGrant(
                permission,
                data_scope,
                project_ids,
                None,
            )
            for permission in permissions
        ),
    )
    return RequestContext(
        user_id=user_id,
        unit_id=unit_id,
        project_id=project_id,
        authorization_context=authorization,
    )


@pytest.fixture(name="sessions")
def sessions_fixture(tmp_path) -> Iterator[sessionmaker[Session]]:
    database_path = tmp_path / "project-skill-access.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, connection_record) -> None:
        del connection_record
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with factory() as session:
        session.add_all(
            [
                Unit(id="unit-1", code="unit-1", name="Unit 1", status="active"),
                Unit(id="unit-2", code="unit-2", name="Unit 2", status="active"),
            ]
        )
        session.flush()
        session.add_all(
            [
                User(id="user-1", display_name="User 1", status="active"),
                User(id="user-2", display_name="User 2", status="active"),
                User(id="user-3", display_name="User 3", status="active"),
            ]
        )
        session.flush()
        session.add_all(
            [
                Project(
                    id="project-1",
                    unit_id="unit-1",
                    code="project-1",
                    name="Project 1",
                    status="active",
                ),
                Project(
                    id="project-2",
                    unit_id="unit-1",
                    code="project-2",
                    name="Project 2",
                    status="active",
                ),
                Project(
                    id="project-inactive",
                    unit_id="unit-1",
                    code="project-inactive",
                    name="Inactive Project",
                    status="inactive",
                ),
                Project(
                    id="project-3",
                    unit_id="unit-2",
                    code="project-3",
                    name="Project 3",
                    status="active",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                UnitMembership(
                    id="unit-membership-1",
                    user_id="user-1",
                    unit_id="unit-1",
                    status="active",
                ),
                UnitMembership(
                    id="unit-membership-2",
                    user_id="user-2",
                    unit_id="unit-1",
                    status="active",
                ),
                UnitMembership(
                    id="unit-membership-3",
                    user_id="user-3",
                    unit_id="unit-2",
                    status="active",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                ProjectMembership(
                    id="project-membership-1",
                    user_id="user-1",
                    unit_id="unit-1",
                    project_id="project-1",
                    status="active",
                ),
                ProjectMembership(
                    id="project-membership-2",
                    user_id="user-2",
                    unit_id="unit-1",
                    project_id="project-2",
                    status="active",
                ),
                ProjectMembership(
                    id="project-membership-3",
                    user_id="user-3",
                    unit_id="unit-2",
                    project_id="project-3",
                    status="active",
                ),
            ]
        )
        session.commit()

    try:
        yield factory
    finally:
        engine.dispose()
