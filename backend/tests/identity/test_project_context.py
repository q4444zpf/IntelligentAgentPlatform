from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.identity.dependencies import require_project_context
from app.identity.schemas import AuthorizationContext, PermissionGrant


def _identity_factory(tmp_path):
    from app.db.base import Base
    from app.identity.models import (
        AuthSession, Permission, Project, ProjectMembership, ProjectMembershipRole,
        Role, RolePermission, Unit, UnitMembership, UnitMembershipRole, User,
    )

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'authorization.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add_all([
        User(id="user-1", display_name="User", status="active", authorization_version=1),
        User(id="user-2", display_name="Other", status="active", authorization_version=1),
        Unit(id="unit-1", code="u1", name="Unit", status="active"),
        Project(id="project-1", unit_id="unit-1", code="p1", name="Project 1", status="active"),
        Project(id="project-2", unit_id="unit-1", code="p2", name="Project 2", status="active"),
        UnitMembership(id="um-1", user_id="user-1", unit_id="unit-1", status="active"),
        UnitMembership(id="um-2", user_id="user-2", unit_id="unit-1", status="active"),
        Role(id="role-unit", unit_id="unit-1", code="unit-role", name="Unit", scope_type="unit", status="active"),
        Role(id="role-project", unit_id="unit-1", code="project-role", name="Project", scope_type="project", status="active"),
        Permission(id="perm-read", code="workflow.read", resource="workflow", action="read", risk_level="low", status="active"),
        Permission(id="perm-audit", code="audit.read", resource="audit", action="read", risk_level="low", status="active"),
        UnitMembershipRole(id="umr-1", user_id="user-1", unit_id="unit-1", role_id="role-unit", scope_type="unit"),
        ProjectMembership(id="pm-1", user_id="user-1", unit_id="unit-1", project_id="project-1", status="active"),
        ProjectMembershipRole(id="pmr-1", user_id="user-1", unit_id="unit-1", project_id="project-1", role_id="role-project", scope_type="project"),
        RolePermission(id="grant-unit", role_id="role-unit", permission_code="audit.read", unit_id="unit-1", data_scope="unit"),
        RolePermission(id="grant-project", role_id="role-project", permission_code="workflow.read", unit_id="unit-1", data_scope="project"),
        AuthSession(id="session-1", session_token_hash="hash-1", user_id="user-1", unit_id="unit-1", current_project_id="project-1", auth_method="dev_test", csrf_secret_encrypted={"v": "x"}, authorization_version=1, idle_expires_at=datetime.now(timezone.utc) + timedelta(hours=1), absolute_expires_at=datetime.now(timezone.utc) + timedelta(hours=1), last_seen_at=datetime.now(timezone.utc)),
    ])
    session.commit()
    return session


def test_repository_loads_active_grant_boundaries_and_version(tmp_path):
    from app.identity.repository import AuthorizationRepository

    session = _identity_factory(tmp_path)
    context = AuthorizationRepository(session).load_context("session-1")
    assert context.current_project_id == "project-1"
    assert context.authorization_version == 1
    assert context.role_codes == ("project-role", "unit-role")
    assert any(grant.permission_code == "workflow.read" and grant.project_ids == frozenset({"project-1"}) for grant in context.grants)
    assert any(grant.permission_code == "audit.read" and grant.data_scope == "unit" for grant in context.grants)


def test_repository_auto_selects_only_project_for_new_context(tmp_path):
    from app.identity.models import AuthSession, ProjectMembership
    from app.identity.repository import AuthorizationRepository

    session = _identity_factory(tmp_path)
    session.query(ProjectMembership).filter_by(project_id="project-1").update({"status": "inactive"})
    session.query(AuthSession).filter_by(id="session-1").update({"current_project_id": None})
    session.commit()
    assert AuthorizationRepository(session).load_context("session-1").current_project_id is None
    session.query(ProjectMembership).filter_by(project_id="project-1").update({"status": "active"})
    session.commit()
    assert AuthorizationRepository(session).load_context("session-1").current_project_id == "project-1"


@pytest.mark.parametrize("field, value", [
    ("user", "inactive"), ("membership", "inactive"), ("role", "inactive"),
    ("permission", "inactive"), ("project", "inactive"),
])
def test_repository_excludes_inactive_identity_inputs(tmp_path, field, value):
    from app.identity.models import Permission, Project, Role, UnitMembership, User
    from app.identity.repository import AuthorizationRepository

    session = _identity_factory(tmp_path)
    model = {"user": User, "membership": UnitMembership, "role": Role, "permission": Permission, "project": Project}[field]
    if field == "role":
        record = session.query(model).filter_by(code="project-role").one()
    elif field == "permission":
        record = session.query(model).filter_by(code="workflow.read").one()
    elif field == "project":
        record = session.query(model).filter_by(id="project-1").one()
    else:
        record = session.query(model).first()
    record.status = value
    session.commit()
    if field in {"user", "membership"}:
        with pytest.raises(LookupError):
            AuthorizationRepository(session).load_context("session-1")
    else:
        context = AuthorizationRepository(session).load_context("session-1")
        assert not any(grant.permission_code == "workflow.read" for grant in context.grants)


def _context(project_id: str | None) -> AuthorizationContext:
    return AuthorizationContext(
        session_id="s1",
        user_id="u1",
        unit_id="unit-1",
        current_project_id=project_id,
        auth_method="dev_test",
        authorization_version=1,
        role_codes=(),
        grants=(PermissionGrant("workflow.read", "unit", frozenset(), None),),
    )


@pytest.mark.parametrize("project_id, status", [(None, 409), ("project-1", 200)])
def test_project_dependency_requires_a_valid_current_project(project_id, status):
    app = FastAPI()

    @app.middleware("http")
    async def attach_context(request: Request, call_next):
        request.state.authorization_context = _context(project_id)
        return await call_next(request)

    from fastapi import Depends

    @app.get("/project")
    def project(context: AuthorizationContext = Depends(require_project_context)):
        return {"project_id": context.current_project_id}

    assert TestClient(app).get("/project").status_code == status


def test_context_has_no_legacy_project_or_role_properties():
    context = _context("project-1")
    assert context.current_project_id == "project-1"
    assert not hasattr(context, "project_id")
    assert not hasattr(context, "role")
    assert not hasattr(context, "roles")
