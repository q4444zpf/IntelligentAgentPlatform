from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.request_context import (
    RequestContext,
    _cookie_request_context,
    _is_trusted_dev_client,
    require_admin_context,
    require_dev_authorization_context,
    require_request_context,
)
from app.identity.schemas import AuthorizationContext, PermissionGrant


def build_client(allow_dev_identity: bool) -> TestClient:
    app = FastAPI()

    @app.get("/context")
    def context(value: RequestContext = Depends(require_request_context)):
        return value

    @app.post("/admin")
    def admin(_value: RequestContext = Depends(require_admin_context)):
        return {"ok": True}

    app.state.allow_dev_identity = allow_dev_identity
    return TestClient(app)


def test_rejects_missing_identity():
    assert build_client(True).get("/context").status_code == 401


def test_accepts_explicitly_trusted_docker_client():
    assert _is_trusted_dev_client("172.28.0.1", ("172.28.0.0/16",))
    assert not _is_trusted_dev_client("192.168.1.10", ("172.28.0.0/16",))


def test_rejects_headers_when_dev_identity_is_disabled():
    response = build_client(False).get(
        "/context",
        headers={"X-Unit-ID": "unit-1", "X-User-ID": "user-1", "X-Project-ID": "project-1"},
    )
    assert response.status_code == 401


def test_accepts_explicit_dev_identity():
    response = build_client(True).get(
        "/context",
        headers={"X-Unit-ID": "unit-1", "X-User-ID": "user-1", "X-Project-ID": "project-1", "X-User-Roles": "user, unit_auditor"},
    )
    body = response.json()
    assert {key: body[key] for key in ("unit_id", "user_id", "project_id")} == {
        "unit_id": "unit-1", "user_id": "user-1", "project_id": "project-1"
    }
    assert set(body["roles"]) == {"user", "unit_auditor"}


def test_requires_unit_header_for_dev_identity():
    response = build_client(True).get("/context", headers={"X-User-ID": "user-1", "X-Project-ID": "project-1"})
    assert response.status_code == 401


def test_maps_legacy_admin_role_to_project_admin():
    response = build_client(True).get("/context", headers={"X-Unit-ID": "unit-1", "X-User-ID": "user-1", "X-Project-ID": "project-1", "X-User-Role": "admin"})
    assert response.status_code == 200
    assert response.json()["roles"] == ["project_admin"]


def test_rejects_unknown_role():
    response = build_client(True).get("/context", headers={"X-Unit-ID": "unit-1", "X-User-ID": "user-1", "X-Project-ID": "project-1", "X-User-Roles": "user,superuser"})
    assert response.status_code == 401


def test_rejects_unknown_legacy_role_when_modern_roles_are_valid():
    response = build_client(True).get(
        "/context",
        headers={
            "X-Unit-ID": "unit-1",
            "X-User-ID": "user-1",
            "X-Project-ID": "project-1",
            "X-User-Roles": "user,project_admin",
            "X-User-Role": "superuser",
        },
    )
    assert response.status_code == 401


def test_role_codes_are_a_sorted_immutable_snapshot():
    assert RequestContext(unit_id="unit-1", project_id="project-1", user_id="user-1").role == "user"
    assert RequestContext(unit_id="unit-1", project_id="project-1", user_id="user-1", roles=frozenset({"unit_auditor"})).role == "user"
    assert RequestContext(unit_id="unit-1", project_id="project-1", user_id="user-1", roles=frozenset({"project_admin"})).role == "admin"
    assert RequestContext(
        unit_id="unit-1",
        project_id="project-1",
        user_id="user-1",
        roles=frozenset({"user", "project_admin"}),
    ).role_codes == ("project_admin", "user")
    assert RequestContext(
        unit_id="unit-1", project_id="project-1", user_id="user-1"
    ).role_codes == ("user",)


def test_admin_dependency_rejects_unit_auditor_and_accepts_project_admin():
    client = build_client(True)
    base = {"X-Unit-ID": "unit-1", "X-User-ID": "user-1", "X-Project-ID": "project-1"}
    assert client.post("/admin", headers={**base, "X-User-Roles": "unit_auditor"}).status_code == 403
    assert client.post("/admin", headers={**base, "X-User-Roles": "project_admin"}).status_code == 200


def test_dev_authorization_adapter_returns_scoped_context_without_legacy_fields():
    app = FastAPI()

    @app.get("/context")
    def context(value=Depends(require_dev_authorization_context)):
        return {
            "project": value.current_project_id,
            "can_run": any(grant.permission_code == "agent.run" for grant in value.grants),
            "has_legacy_project": hasattr(value, "project_id"),
        }

    app.state.allow_dev_identity = True
    response = TestClient(app).get(
        "/context",
        headers={
            "X-Unit-ID": "unit-1",
            "X-User-ID": "user-1",
            "X-Project-ID": "project-1",
            "X-User-Roles": "user",
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "project": "project-1",
        "can_run": False,
        "has_legacy_project": False,
    }


def test_dev_unit_admin_header_retains_unit_scoped_collaboration_grants():
    app = FastAPI()

    @app.get("/grants")
    def grants(value: RequestContext = Depends(require_request_context)):
        assert value.authorization_context is not None
        return [
            {"code": grant.permission_code, "scope": grant.data_scope}
            for grant in value.authorization_context.grants
            if grant.permission_code == "collaboration.manage"
        ]

    app.state.allow_dev_identity = True
    response = TestClient(app).get(
        "/grants",
        headers={
            "X-Unit-ID": "unit-1",
            "X-User-ID": "user-1",
            "X-Project-ID": "project-1",
            "X-User-Roles": "unit_admin",
        },
    )

    assert response.status_code == 200
    assert response.json() == [{"code": "collaboration.manage", "scope": "unit"}]


def test_cookie_context_retains_server_authorization_snapshot(monkeypatch):
    class CookieSession:
        def __init__(self):
            self.values = iter((
                type("Auth", (), {
                    "user_id": "user-1",
                    "id": "server-session",
                    "unit_id": "unit-1",
                    "idle_expires_at": None,
                    "absolute_expires_at": None,
                    "authorization_version": 1,
                })(),
                type("Membership", (), {"status": "active"})(),
            ))

        def scalar(self, _query):
            return next(self.values)

        def get(self, model, _value):
            if model.__name__ == "User":
                return type(
                    "User",
                    (),
                    {"id": "user-1", "status": "active", "authorization_version": 1},
                )()
            return None

    authorization = AuthorizationContext(
        session_id="server-session",
        user_id="user-1",
        unit_id="unit-1",
        current_project_id="project-1",
        auth_method="local",
        authorization_version=1,
        role_codes=("unit_admin",),
        grants=(PermissionGrant("collaboration.run", "unit", frozenset(), None),),
    )
    from app.identity.repository import AuthorizationRepository

    monkeypatch.setattr(
        AuthorizationRepository,
        "load_context",
        lambda _self, _session_id: authorization,
    )

    context = _cookie_request_context(CookieSession(), "cookie-token")

    assert context.authorization_context is authorization
    assert context.role_codes == ("unit_admin",)
    assert context.authorization_context.grants[0].data_scope == "unit"
