import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.control.executors import ControlExecutorRegistry
from app.control.service import ControlService
from app.core.database import get_session
from app.core.request_context import RequestContext, require_request_context
from app.db.base import Base
from app.identity.schemas import AuthorizationContext, PermissionGrant
from app.main import app
import app.main as main_module


CONFIG = json.dumps({"reservoir": {"base_url": "https://control.example/api", "token_env": "IAP_CONTROL_EXECUTOR_RESERVOIR_TOKEN", "timeout_seconds": 10}})


def configured_registry(handler, *, enabled=True):
    return ControlExecutorRegistry.from_json(
        CONFIG, lambda _name: "test-token", "production",
        http_transport=httpx.MockTransport(handler), enabled=enabled,
    )


def test_router_injects_configured_registry_into_each_control_service(monkeypatch):
    registry = configured_registry(lambda _request: httpx.Response(200, json={"status": "ok"}))
    monkeypatch.setattr(main_module, "control_registry", registry)
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(engine)
    created = []

    def tracking_service(db_session, *, executors):
        service = ControlService(db_session, executors=executors)
        created.append(service)
        return service

    authorization = AuthorizationContext(
        session_id="test", user_id="u1", unit_id="unit-1", current_project_id="project-1",
        auth_method="dev_test", authorization_version=1, role_codes=("viewer",),
        grants=(PermissionGrant("control.read", "project", frozenset({"project-1"}), None),),
    )
    context = RequestContext(
        user_id="u1", unit_id="unit-1", project_id="project-1",
        roles=frozenset({"user"}), authorization_context=authorization,
    )
    monkeypatch.setattr(main_module, "ControlService", tracking_service)
    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_request_context] = lambda: context
    try:
        client = TestClient(app)
        assert client.get("/api/control/policies").json() == []
        assert client.get("/api/control/policies").json() == []
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        session.close()
        engine.dispose()
    assert len(created) == 2
    assert created[0] is not created[1]
    assert all(set(service.executors) == {"reservoir"} for service in created)
    assert all(service.executors["reservoir"] is registry.executors["reservoir"] for service in created)


def test_health_is_503_when_enabled_executor_readiness_fails(monkeypatch):
    registry = configured_registry(lambda _request: httpx.Response(503))
    monkeypatch.setattr(main_module, "control_registry", registry)
    response = TestClient(app).get("/api/health")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "control": {"ready": False, "unavailable_asset_types": ["reservoir"]}}


def test_health_remains_200_when_control_gateway_is_disabled(monkeypatch):
    registry = ControlExecutorRegistry.from_json(None, lambda _name: None, "production", enabled=False)
    monkeypatch.setattr(main_module, "control_registry", registry)
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "intelligent-agent-platform-api", "version": "0.2.0"}


def test_disabled_control_with_configured_endpoint_does_not_inject_executor(monkeypatch):
    outbound_requests = []

    def handler(request):
        outbound_requests.append(request)
        return httpx.Response(200, json={"status": "ok"})

    registry = configured_registry(handler, enabled=False)
    monkeypatch.setattr(main_module, "control_registry", registry)
    service = main_module.control_service_factory(object())
    assert service.executors == {}
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "intelligent-agent-platform-api", "version": "0.2.0"}
    assert outbound_requests == []


def test_enabled_control_without_executor_configuration_fails_startup(monkeypatch):
    registry = ControlExecutorRegistry.from_json(None, lambda _name: None, "production", enabled=True)
    with pytest.raises(ValueError, match="control gateway requires executor configuration"):
        registry.validate_startup()
    monkeypatch.setattr(main_module, "settings", replace(main_module.settings, control_gateway_enabled=True, control_executors_json=None))
    with pytest.raises(ValueError, match="control gateway requires executor configuration"), TestClient(app):
        pass
