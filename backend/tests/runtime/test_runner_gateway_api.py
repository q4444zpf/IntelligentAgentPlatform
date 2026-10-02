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
        if token == "artifact-create-only" and action != "artifact.create":
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


def test_simulation_attestation_requires_separate_runner_action():
    response = make_client().post(
        "/internal/runner/runs/run-1/artifact-attestations/simulation",
        headers=bearer("artifact-create-only"),
        json={"team_version_id": "version", "member_agent_id": "member", "task_id": "task", "invocation_id": "invocation", "artifact_ids": ["artifact"], "completed_at": "2026-09-15T10:00:00Z", "capability": "c" * 43},
    )
    assert response.status_code == 403


def test_runner_client_serializes_simulation_attestation_with_idempotency_key():
    import json
    import httpx
    from app.runtime.runner_gateway_client import RunnerGatewayClient
    received = []
    def handle(request):
        received.append(request)
        return httpx.Response(200, json=[{"path": "/artifacts/simulation.json", "artifact_id": "artifact", "size_bytes": 2, "sha256": "a" * 64, "content_type": "application/json"}])
    client = RunnerGatewayClient("http://runner/internal/runner", "run-1", "token", transport=httpx.MockTransport(handle))
    result = client.attest_simulation_artifacts(team_version_id="version", member_agent_id="member", task_id="task", invocation_id="invocation", artifact_ids=("artifact",), completed_at=datetime(2026, 9, 15, 10, tzinfo=UTC), capability="c" * 43, idempotency_key="attest-task")
    assert result[0].artifact_id == "artifact"
    assert received[0].url.path == "/internal/runner/runs/run-1/artifact-attestations/simulation"
    assert received[0].headers["Idempotency-Key"] == "attest-task"
    assert json.loads(received[0].content)["artifact_ids"] == ["artifact"]


def test_snapshot_digest_mismatch_is_409():
    snapshot = build_snapshot().model_copy(update={"digest": "b" * 64})

    response = make_client(snapshot).get(
        "/internal/runner/runs/run-1/snapshot", headers=bearer("valid")
    )

    assert response.status_code == 409
    assert response.json()["code"] == "snapshot_invalid"


def test_model_endpoint_requires_model_invoke_action():
    response = make_client().post(
        "/internal/runner/runs/run-1/model-invocations",
        headers={
            **bearer("model-only"),
            "Idempotency-Key": "model-1",
        },
        json={
            "messages": [{"role": "user", "content": "test"}],
            "tools": [],
            "invocation_sequence": 0,
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "runner_action_forbidden"


def test_tool_endpoint_requires_tool_invoke_action():
    response = make_client().post(
        "/internal/runner/runs/run-1/tool-invocations",
        headers={
            **bearer("model-only"),
            "Idempotency-Key": "tool-1",
        },
        json={
            "tool_call_id": "call-1",
            "tool_id": "water.query_level",
            "version": "1",
            "arguments": {},
            "invocation_sequence": 0,
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "runner_action_forbidden"
