from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.runtime.execution_snapshot import (
    ExecutionSnapshotPayload,
    PublishedAgentSnapshot,
    SnapshotModelSelection,
    SnapshotRuntimeLimits,
    StoredExecutionSnapshot,
    canonical_snapshot_bytes,
)
from app.runtime.run_tokens import RunTokenClaims, RunTokenForbidden, RunTokenNotFound
from app.runtime.runner_gateway_auth import (
    RunnerGatewayError,
    runner_gateway_error_handler,
)
from app.runtime.runner_gateway_router import create_router


def build_snapshot():
    payload = ExecutionSnapshotPayload(
        snapshot_id="snapshot-1",
        run_id="run-1",
        unit_id="unit-1",
        project_id="project-1",
        user_id="user-1",
        actor=PublishedAgentSnapshot(
            id="agent-1",
            name="Agent",
            description="",
            runtime_form="common",
            language="zh-CN",
            system_prompt="",
            context_prompt="",
            approval_policy="never",
        ),
        model=SnapshotModelSelection(provider_id="provider-1", model="model-1"),
        messages=(),
        limits=SnapshotRuntimeLimits(snapshot_max_bytes=1048576),
        created_at=datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
    )
    import hashlib

    return StoredExecutionSnapshot(
        snapshot_id=payload.snapshot_id,
        run_id=payload.run_id,
        digest=hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest(),
        payload=payload,
        created_at=payload.created_at,
        expires_at=None,
    )


class FakeTokenService:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def verify(self, token, run_id, action):
        if run_id != "run-1":
            raise RunTokenNotFound(run_id)
        if token == "model-only":
            raise RunTokenForbidden("forbidden")
        return RunTokenClaims(
            iss="iap-api",
            aud="iap-runner-gateway",
            jti="token-1",
            run_id=run_id,
            unit_id="unit-1",
            project_id="project-1",
            snapshot_id=self.snapshot.snapshot_id,
            snapshot_digest=self.snapshot.digest,
            actions=("snapshot.read",),
            iat=1,
            nbf=1,
            exp=9999999999,
        )


class FakeSnapshotService:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def get(self, snapshot_id):
        return self.snapshot if snapshot_id == self.snapshot.snapshot_id else None


def make_client(snapshot=None):
    snapshot = snapshot or build_snapshot()
    app = FastAPI()
    app.add_exception_handler(RunnerGatewayError, runner_gateway_error_handler)
    app.include_router(
        create_router(
            token_service_dependency=lambda: FakeTokenService(snapshot),
            snapshot_service_dependency=lambda: FakeSnapshotService(snapshot),
        ),
        prefix="/internal/runner",
    )
    return TestClient(app)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_snapshot_endpoint_returns_verified_payload():
    snapshot = build_snapshot()
    response = make_client(snapshot).get(
        "/internal/runner/runs/run-1/snapshot", headers=bearer("valid")
    )

    assert response.status_code == 200
    assert response.json()["digest"] == snapshot.digest
    assert response.json()["payload"]["run_id"] == "run-1"


def test_cross_run_is_404_and_missing_action_is_403():
    client = make_client()

    assert client.get(
        "/internal/runner/runs/run-2/snapshot", headers=bearer("valid")
    ).status_code == 404
    assert client.get(
        "/internal/runner/runs/run-1/snapshot", headers=bearer("model-only")
    ).status_code == 403


def test_snapshot_digest_mismatch_is_409():
    snapshot = build_snapshot().model_copy(update={"digest": "b" * 64})

    response = make_client(snapshot).get(
        "/internal/runner/runs/run-1/snapshot", headers=bearer("valid")
    )

    assert response.status_code == 409
    assert response.json()["code"] == "snapshot_invalid"
