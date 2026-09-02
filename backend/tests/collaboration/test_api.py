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
