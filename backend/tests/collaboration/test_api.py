from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.collaboration.models import Team
from app.collaboration.router import _service, router
from app.collaboration.service import TeamService
from app.core.database import get_session
from app.core.request_context import RequestContext, require_request_context
from app.identity.schemas import AuthorizationContext, PermissionGrant


def _context(*permissions: str) -> RequestContext:
    authorization = AuthorizationContext(
        session_id="test-session",
        user_id="u1",
        unit_id="unit-1",
        current_project_id="p1",
        auth_method="dev_test",
        authorization_version=1,
        role_codes=("viewer",),
        grants=tuple(
            PermissionGrant(permission, "project", frozenset({"p1"}), None)
            for permission in permissions
        ),
    )
    return RequestContext(
        user_id="u1",
        unit_id="unit-1",
        project_id="p1",
        authorization_context=authorization,
    )


def test_team_create_returns_403_when_context_lacks_collaboration_manage():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.db.base import Base

    Base.metadata.create_all(engine)
    session = Session(engine)
    authorization = AuthorizationContext(
        session_id="test-session",
        user_id="u1",
        unit_id="unit-1",
        current_project_id="p1",
        auth_method="dev_test",
        authorization_version=1,
        role_codes=("viewer",),
        grants=(PermissionGrant("collaboration.read", "project", frozenset({"p1"}), None),),
    )
    context = RequestContext(
        user_id="u1",
        unit_id="unit-1",
        project_id="p1",
        authorization_context=authorization,
    )
    app = FastAPI()
    app.include_router(router, prefix="/api/collaboration")
    app.dependency_overrides[require_request_context] = lambda: context
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[_service] = lambda: TeamService(
        session,
        agent_service=object(),
        provider_service=object(),
    )

    response = TestClient(app).post("/api/collaboration/teams", json={"name": "联合研判"})

    assert response.status_code == 403
    assert session.scalar(select(func.count()).select_from(Team)) == 0


def test_team_list_applies_enabled_and_published_query_filters():
    class FilteredTeamService:
        teams = [
            {"id": "draft-enabled", "enabled": True, "published_version": None},
            {"id": "published-disabled", "enabled": False, "published_version": 1},
            {"id": "published-enabled", "enabled": True, "published_version": 1},
        ]

        def list(self, context, *, enabled=None, published=None):
            return [
                team for team in self.teams
                if (enabled is None or team["enabled"] is enabled)
                and (published is None or (team["published_version"] is not None) is published)
            ]

    app = FastAPI()
    app.include_router(router, prefix="/api/collaboration")
    app.dependency_overrides[require_request_context] = lambda: _context("collaboration.read")
    app.dependency_overrides[_service] = FilteredTeamService

    response = TestClient(app).get(
        "/api/collaboration/teams?enabled=true&published=true"
    )

    assert response.status_code == 200
    assert [team["id"] for team in response.json()] == ["published-enabled"]


def test_team_create_rejects_a_client_supplied_identifier():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.db.base import Base

    Base.metadata.create_all(engine)
    session = Session(engine)
    app = FastAPI()
    app.include_router(router, prefix="/api/collaboration")
    app.dependency_overrides[require_request_context] = lambda: _context("collaboration.manage")
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[_service] = lambda: TeamService(
        session,
        agent_service=object(),
        provider_service=object(),
    )

    response = TestClient(app).post(
        "/api/collaboration/teams",
        json={"id": "forged-team", "name": "联合研判"},
    )

    assert response.status_code == 422
    assert session.scalar(select(func.count()).select_from(Team)) == 0
