from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.control.router import router, _service
from app.control.errors import (ControlError, ControlNotFoundError, ControlCommandExpired,
                                ControlExecutionInProgress, ControlForbiddenError as ForbiddenError,
                                ControlValidationError as ValidationError, ControlIdempotencyConflict as IdempotencyError,
                                ControlExecutorUnavailable as ExecutorUnavailable, ControlExecutionError as ExecutionError,
                                control_error_handler, unexpected_error_handler)
from app.control.schemas import ControlFeedbackRequest
from app.control.service import (
    ControlExecutionError,
    ControlForbiddenError,
    ControlIdempotencyConflict,
    ControlExecutorUnavailable,
    ControlService,
    ControlValidationError,
)
from app.core.database import get_session
from app.core.request_context import RequestContext, require_request_context
from app.db.base import Base
from app.identity.schemas import AuthorizationContext, PermissionGrant
from backend.tests.control.test_service import FakeExecutor, _artifact, _context as service_context, _policy_request, _propose_request


def _context(*permissions: str, project_id: str = "project-1") -> RequestContext:
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
    app.add_exception_handler(ControlError, control_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
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
    assert response.json()["code"] == "control_not_found"


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
            raise ControlExecutorUnavailable()

    client.app.dependency_overrides[_service] = lambda: FailingService()
    policy = {"asset_type": "gate", "command_type": "set", "parameter_schema": {"type": "object"}, "max_command_lifetime_seconds": 60}
    command = {"policy_id": "p", "target": {"asset_type": "gate"}, "parameters": {}, "simulation_artifact_id": "a", "valid_from": "2026-01-01T00:00:00Z", "valid_until": "2026-01-01T00:00:30Z", "idempotency_key": "k"}
    forbidden = client.post("/api/control/policies", json=policy)
    assert (forbidden.status_code, forbidden.json()["code"]) == (403, "control_forbidden")
    conflict = client.post("/api/control/commands", json=command)
    assert (conflict.status_code, conflict.json()["code"]) == (409, "control_idempotency_conflict")
    invalid = client.post("/api/control/commands/c/approve", json={})
    assert (invalid.status_code, invalid.json()["code"]) == (422, "control_validation_failed")
    response = client.post("/api/control/commands/c/execute", headers={"Idempotency-Key": "k"})
    assert (response.status_code, response.json()["code"]) == (503, "control_executor_unavailable")
    assert "infrastructure" not in response.text


def test_approve_body_cannot_contain_contradictory_decision(client):
    response = client.post("/api/control/commands/missing/approve", json={"decision": "reject"})
    assert response.status_code == 422


def test_policy_detail_patch_and_filters_are_project_scoped(client):
    session = client.app.dependency_overrides[get_session]()
    from app.control.models import ControlPolicy
    session.add_all([
        ControlPolicy(id="p1", unit_id="unit-1", project_id="project-1", owner_id="u1", asset_type="gate", command_type="set", parameter_schema={"type": "object"}, max_command_lifetime_seconds=60, approval_mode="single", deviation_thresholds={}, enabled=True),
        ControlPolicy(id="p2", unit_id="unit-1", project_id="other", owner_id="u1", asset_type="pump", command_type="stop", parameter_schema={"type": "object"}, max_command_lifetime_seconds=60, approval_mode="single", deviation_thresholds={}, enabled=True),
    ])
    session.commit()
    assert [p["id"] for p in client.get("/api/control/policies?asset_type=gate&enabled=true").json()] == ["p1"]
    assert [p["id"] for p in client.get("/api/control/policies?command_type=set").json()] == ["p1"]
    assert client.get("/api/control/policies/p2").status_code == 404
    assert client.patch("/api/control/policies/p2", json={"enabled": False}).status_code == 404
    patched = client.patch("/api/control/policies/p1", json={"enabled": False, "max_command_lifetime_seconds": 120})
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False
    assert patched.json()["max_command_lifetime_seconds"] == 120
    assert [p["id"] for p in client.get("/api/control/policies?enabled=false").json()] == ["p1"]


def test_command_filters_and_detail_return_approval_attempt_feedback_progress(client):
    session = client.app.dependency_overrides[get_session]()
    executor = FakeExecutor()
    manager = ControlService(session, executors={"reservoir": executor})
    admin = service_context("admin", roles=("project_admin",))
    _artifact(session)
    policy = manager.create_policy(admin, _policy_request())
    command = manager.propose(service_context(), _propose_request(policy.id))
    manager.decide(command.id, service_context("reviewer", roles=("project_admin",)), "approve")
    manager.execute(command.id, admin, "execute-1")
    manager.record_feedback(command.id, service_context(), ControlFeedbackRequest(actual_state={"amount": 20}))
    response = client.get(f"/api/control/commands?status=succeeded&policy_id={policy.id}")
    assert response.status_code == 200
    assert [(row["id"], row["approval_count"], row["required_approvals"]) for row in response.json()] == [(command.id, 1, 1)]
    assert client.get("/api/control/commands?status=failed").json() == []
    assert client.get("/api/control/commands?created_after=2099-01-01T00:00:00Z").json() == []
    assert client.get("/api/control/commands?created_before=2000-01-01T00:00:00Z").json() == []
    detail = client.get(f"/api/control/commands/{command.id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["policy_snapshot"]["asset_type"] == "reservoir"
    assert body["approval_decisions"][0]["decision"] == "approve"
    assert [(attempt["kind"], attempt["outcome"]) for attempt in body["attempts"]] == [("execute", "succeeded")]
    assert body["feedback_records"][0]["actual_state"] == {"amount": 20}
    assert body["simulation_attestation"]["status"] == "succeeded"
    client.app.dependency_overrides[require_request_context] = lambda: _context("control.read", project_id="other")
    assert client.get(f"/api/control/commands/{command.id}").status_code == 404


def test_expired_execute_returns_control_command_expired_with_http_410(client):
    session = client.app.dependency_overrides[get_session]()
    now = datetime.now(timezone.utc)
    manager = ControlService(session, clock=lambda: now)
    admin = service_context("admin", roles=("project_admin",))
    _artifact(session)
    policy = manager.create_policy(admin, _policy_request())
    command = manager.propose(service_context(), _propose_request(policy.id, valid_from=now, valid_until=now + timedelta(minutes=1)))
    manager.decide(command.id, service_context("reviewer", roles=("project_admin",)), "approve")
    client.app.dependency_overrides[_service] = lambda: ControlService(session, clock=lambda: now + timedelta(minutes=2))
    response = client.post(f"/api/control/commands/{command.id}/execute", headers={"Idempotency-Key": "execute-1"})
    assert (response.status_code, response.json()["code"]) == (410, "control_command_expired")
    assert response.json()["details"] == {}


@pytest.mark.parametrize("error,status_code,code", [
    (ControlNotFoundError(), 404, "control_not_found"),
    (ForbiddenError("sensitive fixture"), 403, "control_forbidden"),
    (IdempotencyError("sensitive fixture"), 409, "control_idempotency_conflict"),
    (ValidationError("sensitive fixture"), 422, "control_validation_failed"),
    (ControlCommandExpired(), 410, "control_command_expired"),
    (ExecutorUnavailable(), 503, "control_executor_unavailable"),
    (ExecutionError("sensitive fixture"), 409, "control_invalid_state"),
    (ControlExecutionInProgress(), 409, "control_execution_in_progress"),
])
def test_each_documented_control_error_has_stable_code_and_safe_message(client, error, status_code, code):
    class FailingService:
        def create_policy(self, *_args):
            raise error
    client.app.dependency_overrides[_service] = lambda: FailingService()
    response = client.post("/api/control/policies", json={"asset_type": "gate", "command_type": "set", "parameter_schema": {"type": "object"}, "max_command_lifetime_seconds": 60})
    assert response.status_code == status_code
    assert response.json() == {"code": code, "message": error.message, "details": {}}
    assert "sensitive fixture" not in response.text


def test_unexpected_database_error_is_logged_and_returns_500(client, caplog):
    class BrokenService:
        def create_policy(self, *_args):
            raise OperationalError("database statement", {}, RuntimeError("private database token"))
    client.app.dependency_overrides[_service] = lambda: BrokenService()
    client.app.debug = False
    from fastapi.testclient import TestClient
    with TestClient(client.app, raise_server_exceptions=False) as safe_client:
        response = safe_client.post("/api/control/policies", json={"asset_type": "gate", "command_type": "set", "parameter_schema": {"type": "object"}, "max_command_lifetime_seconds": 60})
    assert response.status_code == 500
    assert "private database token" not in response.text
    assert "private database token" in caplog.text
