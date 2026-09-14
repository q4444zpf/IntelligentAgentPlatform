from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.control.router import router, _service
from app.control.service import (
    ControlExecutionError,
    ControlForbiddenError,
    ControlIdempotencyConflict,
    ControlService,
    ControlValidationError,
)
from app.core.database import get_session
from app.core.request_context import RequestContext, require_request_context
from app.db.base import Base
from app.identity.schemas import AuthorizationContext, PermissionGrant


def _context(*permissions: str) -> RequestContext:
    project_id = "project-1"
    authorization = AuthorizationContext(
        session_id="test", user_id="u1", unit_id="unit-1",
        current_project_id=project_id, auth_method="dev_test",
        authorization_version=1, role_codes=("viewer",),
        grants=tuple(PermissionGrant(code, "project", frozenset({project_id}), None) for code in permissions),
    )
    return RequestContext(user_id="u1", unit_id="unit-1", project_id=project_id,
                          roles=frozenset({"user"}), authorization_context=authorization)


@pytest.fixture
def client():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(engine)
    app = FastAPI()
    app.include_router(router, prefix="/api/control")
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_request_context] = lambda: _context("control.read", "control.manage", "control.propose", "control.approve", "control.execute", "control.feedback")
    app.dependency_overrides[_service] = lambda: ControlService(session)
    yield TestClient(app)
    session.close()


def test_control_policy_list_and_safe_not_found(client):
    response = client.get("/api/control/policies")
    assert response.status_code == 200
    assert response.json() == []
    response = client.get("/api/control/commands/missing")
    assert response.status_code == 404
    assert response.json()["detail"] == "Control command does not exist or is not visible"


def test_execute_requires_idempotency_header(client):
    response = client.post("/api/control/commands/cmd-1/execute")
    assert response.status_code == 422


def test_stable_service_error_mapping(client, monkeypatch):
    class FailingService:
        def create_policy(self, *args):
            raise ControlForbiddenError("sensitive infrastructure detail")
        def propose(self, *args):
            raise ControlIdempotencyConflict("duplicate")
        def decide(self, *args):
            raise ControlValidationError("invalid state")
        def execute(self, *args):
            raise ControlExecutionError("no executor registered")

    client.app.dependency_overrides[_service] = lambda: FailingService()
    policy = {"asset_type": "gate", "command_type": "set", "parameter_schema": {"type": "object"}, "max_command_lifetime_seconds": 60}
    command = {"policy_id": "p", "target": {"asset_type": "gate"}, "parameters": {}, "simulation_artifact_id": "a", "valid_from": "2026-01-01T00:00:00Z", "valid_until": "2026-01-01T00:00:30Z", "idempotency_key": "k"}
    assert client.post("/api/control/policies", json=policy).status_code == 403
    assert client.post("/api/control/commands", json=command).status_code == 409
    assert client.post("/api/control/commands/c/approve", json={}).status_code == 422
    response = client.post("/api/control/commands/c/execute", headers={"Idempotency-Key": "k"})
    assert response.status_code == 503
    assert "infrastructure" not in response.text
