from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.runtime.run_tokens import (
    RunTokenClaims,
    RunTokenForbidden,
    RunTokenInvalid,
    RunTokenNotFound,
)
from app.runtime.runner_gateway_auth import (
    RunnerGatewayError,
    require_runner_action,
    runner_gateway_error_handler,
    validate_runner_gateway_startup,
)


CLAIMS = RunTokenClaims(
    iss="iap-api",
    aud="iap-runner-gateway",
    jti="token-1",
    run_id="run-1",
    unit_id="unit-1",
    project_id="project-1",
    snapshot_id="snapshot-1",
    snapshot_digest="a" * 64,
    actions=("snapshot.read",),
    iat=int(datetime.now(UTC).timestamp()),
    nbf=int(datetime.now(UTC).timestamp()),
    exp=int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
)


class FakeTokenService:
    def verify(self, token, run_id, action):
        if token == "expired":
            raise RunTokenInvalid("Runner token is expired")
        if token == "forbidden":
            raise RunTokenForbidden("forbidden")
        if token == "other-run":
            raise RunTokenNotFound(run_id)
        if token != "valid":
            raise RunTokenInvalid("invalid")
        return CLAIMS


def make_client():
    app = FastAPI()
    app.add_exception_handler(RunnerGatewayError, runner_gateway_error_handler)
    service = FakeTokenService()

    @app.get("/runs/{run_id}")
    def route(
        run_id: str,
        claims=Depends(require_runner_action("snapshot.read", lambda: service)),
    ):
        return {"run_id": run_id, "jti": claims.jti}

    return TestClient(app)


def test_runner_auth_returns_claims_for_valid_bearer_token():
    response = make_client().get(
        "/runs/run-1", headers={"Authorization": "Bearer valid"}
    )

    assert response.status_code == 200
    assert response.json() == {"run_id": "run-1", "jti": "token-1"}


def test_runner_auth_returns_stable_safe_error_bodies():
    client = make_client()

    assert client.get("/runs/run-1").json() == {
        "code": "run_token_invalid",
        "message": "Runner 凭证无效",
    }
    assert client.get(
        "/runs/run-1", headers={"Authorization": "Bearer expired"}
    ).json()["code"] == "run_token_expired"
    assert client.get(
        "/runs/run-1", headers={"Authorization": "Bearer forbidden"}
    ).status_code == 403
    assert client.get(
        "/runs/run-2", headers={"Authorization": "Bearer other-run"}
    ).status_code == 404


def test_startup_requires_signing_key_when_sandbox_is_enabled(monkeypatch):
    monkeypatch.setenv("IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED", "true")
    monkeypatch.delenv("IAP_RUNNER_TOKEN_SIGNING_KEY", raising=False)

    with pytest.raises(ValueError, match="at least 32 bytes"):
        validate_runner_gateway_startup()


def test_startup_does_not_require_runner_key_when_sandbox_is_disabled(monkeypatch):
    monkeypatch.setenv("IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED", "false")
    monkeypatch.delenv("IAP_RUNNER_TOKEN_SIGNING_KEY", raising=False)

    validate_runner_gateway_startup()
