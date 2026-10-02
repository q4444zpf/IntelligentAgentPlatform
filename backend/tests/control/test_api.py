from datetime import datetime, timedelta, timezone
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError, OperationalError
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


@pytest.mark.parametrize("threshold", [True, "5", -1, None, float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("operation", ["create", "update"])
def test_policy_api_rejects_invalid_thresholds(client, threshold, operation):
    body = _policy_request().model_dump()
    if operation == "create":
        body["deviation_thresholds"] = {"amount": threshold}
        response = client.post("/api/control/policies", content=json.dumps(body), headers={"Content-Type": "application/json"})
    else:
        policy = client.post("/api/control/policies", json=body).json()
        response = client.patch(f"/api/control/policies/{policy['id']}", content=json.dumps({"deviation_thresholds": {"amount": threshold}}), headers={"Content-Type": "application/json"})
    assert (response.status_code, response.json().get("code")) == (422, "control_validation_failed")


@pytest.mark.parametrize("kind", ["execute", "rollback"])
@pytest.mark.parametrize("interruption", ["timeout", "process_exit"])
def test_refreshed_api_exposes_original_recovery_key_and_reconciles_once(client, kind, interruption):
    from tests.control.test_execution_recovery import TimeoutExecutor

    class ProcessInterrupted(BaseException):
        pass

    class RecoverableExecutor(TimeoutExecutor):
        def execute(self, command, external_idempotency_key):
            try:
                return super().execute(command, external_idempotency_key)
            except TimeoutError:
                if interruption == "process_exit":
                    raise ProcessInterrupted()
                raise

        def rollback(self, command, rollback_plan, external_idempotency_key):
            return self.execute(command, external_idempotency_key)

    session = client.app.dependency_overrides[get_session]()
    executor = RecoverableExecutor()
    manager = ControlService(session, executors={"reservoir": executor})
    client.app.dependency_overrides[_service] = lambda: ControlService(session, executors={"reservoir": executor})
    admin = service_context("admin", roles=("project_admin",))
    _artifact(session)
    policy = manager.create_policy(admin, _policy_request())
    command = manager.propose(service_context(), _propose_request(policy.id))
    manager.decide(command.id, admin, "approve")
    if kind == "rollback":
        executor.result = {"outcome": "failed"}
        with pytest.raises((ControlExecutorUnavailable, ProcessInterrupted)):
            manager.execute(command.id, admin, "execute-before-rollback")
        if interruption == "process_exit":
            assert manager.execute(command.id, admin, "execute-before-rollback").status == "failed"
        executor.result = {"outcome": "succeeded"}
    with pytest.raises((ControlExecutorUnavailable, ProcessInterrupted)):
        getattr(manager, kind)(command.id, admin, f"original-{kind}")
    session.expire_all()
    listed = client.get("/api/control/commands").json()[0]
    detail = client.get(f"/api/control/commands/{command.id}").json()
    expected = {"kind": kind, "request_key": f"original-{kind}"}
    assert listed.get("recovery_operation") == expected
    assert detail.get("recovery_operation") == expected
    headers = {"Idempotency-Key": listed["recovery_operation"]["request_key"]}
    result = client.post(f"/api/control/commands/{command.id}/{kind}", headers=headers)
    assert result.status_code == 200
    assert result.json()["status"] == ("awaiting_feedback" if kind == "execute" else "rolled_back")
    calls = len(executor.calls)
    assert client.post(f"/api/control/commands/{command.id}/{kind}", headers=headers).status_code == 200
    assert len(executor.calls) == calls
    assert len(executor.physical_results) == (1 if kind == "execute" else 2)
    assert client.get("/api/control/commands").json()[0]["recovery_operation"] is None
    client.app.dependency_overrides[require_request_context] = lambda: _context("control.read", project_id="other")
    assert client.get("/api/control/commands").json() == []


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


@pytest.mark.parametrize("operation", ["policy", "proposal"])
def test_unexpected_audit_integrity_error_is_logged_and_returns_500(client, caplog, monkeypatch, operation):
    session = client.app.dependency_overrides[get_session]()
    manager = ControlService(session)
    if operation == "proposal":
        _artifact(session)
        policy = manager.create_policy(service_context("admin", roles=("project_admin",)), _policy_request())
        path = "/api/control/commands"
        body = _propose_request(policy.id).model_dump(mode="json")
    else:
        path = "/api/control/policies"
        body = _policy_request().model_dump(mode="json")

    def fail_audit(*_args, **_kwargs):
        raise IntegrityError("INSERT audit_events", {}, RuntimeError("private audit constraint"))
    monkeypatch.setattr(manager.audit, "record", fail_audit)
    client.app.dependency_overrides[_service] = lambda: manager
    with TestClient(client.app, raise_server_exceptions=False) as safe_client:
        response = safe_client.post(path, json=body)
    assert response.status_code == 500
    assert "private audit constraint" not in response.text
    assert "private audit constraint" in caplog.text


def test_domain_uniqueness_keeps_stable_422_and_409_codes(client):
    session = client.app.dependency_overrides[get_session]()
    policy_body = _policy_request().model_dump(mode="json")
    first = client.post("/api/control/policies", json=policy_body)
    assert first.status_code == 201
    duplicate = client.post("/api/control/policies", json=policy_body)
    assert (duplicate.status_code, duplicate.json()["code"]) == (422, "control_validation_failed")
    _artifact(session)
    request = _propose_request(first.json()["id"]).model_dump(mode="json")
    assert client.post("/api/control/commands", json=request).status_code == 201
    request["parameters"] = {"amount": 21}
    conflict = client.post("/api/control/commands", json=request)
    assert (conflict.status_code, conflict.json()["code"]) == (409, "control_idempotency_conflict")
