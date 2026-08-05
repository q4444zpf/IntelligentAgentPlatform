import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.identity.dependencies import require_project_context
from app.identity.schemas import AuthorizationContext, PermissionGrant


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
