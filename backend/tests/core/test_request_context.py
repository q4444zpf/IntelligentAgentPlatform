from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.request_context import (
    RequestContext,
    require_admin_context,
    require_request_context,
)


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
