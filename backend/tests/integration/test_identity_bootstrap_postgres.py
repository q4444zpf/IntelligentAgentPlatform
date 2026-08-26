import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.identity.bootstrap import BootstrapRequest, bootstrap_initial_unit_admin
from app.identity.models import (
    ExternalIdentity,
    Project,
    ProjectMembership,
    Unit,
    UnitMembership,
    User,
)


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")
def test_bootstrap_persists_parent_rows_before_foreign_key_dependents():
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    suffix = uuid4().hex
    request = BootstrapRequest(
        unit_code=f"postgres-bootstrap-unit-{suffix}",
        unit_name="PostgreSQL Bootstrap Unit",
        user_display_name="PostgreSQL Bootstrap Administrator",
        issuer=f"http://postgres-bootstrap-{suffix}.test",
        subject=f"postgres-bootstrap-admin-{suffix}",
        initial_project_code=f"postgres-bootstrap-project-{suffix}",
        initial_project_name="PostgreSQL Bootstrap Project",
    )

    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                with Session(
                    bind=connection,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                ) as session:
                    user_id = bootstrap_initial_unit_admin(session, request)
                    unit = session.scalar(
                        select(Unit).where(Unit.code == request.unit_code)
                    )
                    project = session.scalar(
                        select(Project).where(
                            Project.code == request.initial_project_code
                        )
                    )
                    identity = session.scalar(
                        select(ExternalIdentity).where(
                            ExternalIdentity.issuer == request.issuer,
                            ExternalIdentity.subject == request.subject,
                        )
                    )
                    unit_membership = session.scalar(
                        select(UnitMembership).where(
                            UnitMembership.user_id == user_id
                        )
                    )
                    project_membership = session.scalar(
                        select(ProjectMembership).where(
                            ProjectMembership.user_id == user_id
                        )
                    )

                    assert session.get(User, user_id) is not None
                    assert unit is not None
                    assert project is not None
                    assert project.unit_id == unit.id
                    assert identity is not None
                    assert identity.user_id == user_id
                    assert unit_membership is not None
                    assert unit_membership.unit_id == unit.id
                    assert project_membership is not None
                    assert project_membership.unit_id == unit.id
                    assert project_membership.project_id == project.id
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
