from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.request_context import RequestContext, require_request_context


def build_client(allow_dev_identity: bool) -> TestClient:
    app = FastAPI()

    @app.get("/context")
    def context(value: RequestContext = Depends(require_request_context)):
        return value

    app.state.allow_dev_identity = allow_dev_identity
    return TestClient(app)


def test_rejects_missing_identity():
    assert build_client(True).get("/context").status_code == 401


def test_rejects_headers_when_dev_identity_is_disabled():
    response = build_client(False).get(
        "/context",
        headers={"X-User-ID": "user-1", "X-Project-ID": "project-1"},
    )
    assert response.status_code == 401


def test_accepts_explicit_dev_identity():
    response = build_client(True).get(
        "/context",
        headers={"X-User-ID": "user-1", "X-Project-ID": "project-1"},
    )
    assert response.json() == {
        "user_id": "user-1", "project_id": "project-1", "role": "user"
    }
