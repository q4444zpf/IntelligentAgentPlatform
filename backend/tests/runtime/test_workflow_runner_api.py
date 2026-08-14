from fastapi.testclient import TestClient

from app.runtime.sandbox_readiness import SandboxReadiness
from app.runtime.workflow_runner_api import (
    build_launcher_client_from_env,
    create_runner_app,
)


def test_runner_health_reports_sandbox_capability():
    client = TestClient(create_runner_app(sandbox_enabled=False))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["sandbox"] is False
    assert "network_disabled" in response.json()["missing"]


def test_runner_rejects_execution_until_sandbox_is_enabled():
    client = TestClient(create_runner_app(sandbox_enabled=False))

    response = client.post("/runs", json=_submission())

    assert response.status_code == 503
    assert response.json() == {"detail": "Sandbox Executor is not enabled"}


def test_runner_accepts_snapshot_reference_when_sandbox_is_enabled():
    client = TestClient(create_runner_app(
        sandbox_enabled=True,
        readiness=SandboxReadiness(True, True, True, True, True, True),
    ))

    response = client.post("/runs", json=_submission())

    assert response.status_code == 200
    assert response.json() == {"run_id": "r1", "status": "accepted"}


def test_runner_can_derive_sandbox_from_inspected_container():
    client = TestClient(create_runner_app(
        sandbox_enabled=True,
        container_info={
            "Config": {"Image": "iap/workflow-runner:v1", "User": "65534", "ReadonlyRootfs": True},
            "HostConfig": {"NetworkMode": "none", "Privileged": False, "CapDrop": ["ALL"], "Memory": 1, "PidsLimit": 1, "NanoCpus": 1},
            "Labels": {"iap.cleanup_guaranteed": "true"},
        },
    ))

    response = client.get("/health")

    assert response.json()["sandbox"] is True


def test_runner_stays_disabled_when_inspected_container_is_missing():
    client = TestClient(create_runner_app(
        sandbox_enabled=True,
        inspect_transport=lambda _name: None,
    ))

    response = client.get("/health")

    assert response.json()["sandbox"] is False
    assert "container_inspection" in response.json()["missing"]


def test_runner_prepares_container_before_accepting_run():
    class FakeLauncherClient:
        def __init__(self): self.runs = []
        def prepare(self, run_id, **execution): self.runs.append((run_id, execution)); return {"run_id": run_id, "status": "running"}
        def inspect(self, run_id): return {"run_id": run_id, "status": "exited", "exit_code": 0, "oom_killed": False}
        def terminate(self, run_id): return {"run_id": run_id, "status": "terminated"}
        def cleanup(self, run_id): return {"run_id": run_id, "status": "cleaned"}

    launcher = FakeLauncherClient()
    client = TestClient(create_runner_app(
        sandbox_enabled=True,
        readiness=SandboxReadiness(True, True, True, True, True, True),
        launcher_client=launcher,
    ))

    response = client.post("/runs", json=_submission())

    assert response.status_code == 200
    assert launcher.runs == [("r1", {
        "agent_version": "a1",
        "checkpoint_key": "c1",
        "deadline_at": "2099-01-01T00:00:00Z",
        "snapshot_id": "snapshot-1",
        "snapshot_digest": "a" * 64,
        "gateway_url": "http://api:8000/internal/runner",
        "run_token": "secret-token",
    })]

    assert client.get("/runs/r1").json()["status"] == "exited"
    assert client.post("/runs/r1/terminate").json()["status"] == "terminated"
    assert client.delete("/runs/r1").json()["status"] == "cleaned"


def test_runner_builds_launcher_client_only_when_url_and_token_are_configured(monkeypatch):
    monkeypatch.delenv("IAP_SANDBOX_LAUNCHER_URL", raising=False)
    monkeypatch.delenv("IAP_RUNNER_LAUNCHER_TOKEN", raising=False)
    assert build_launcher_client_from_env() is None

    monkeypatch.setenv("IAP_SANDBOX_LAUNCHER_URL", "http://sandbox-launcher:8091")
    monkeypatch.setenv("IAP_RUNNER_LAUNCHER_TOKEN", "secret")
    client = build_launcher_client_from_env()
    assert client.transport.base_url == "http://sandbox-launcher:8091"


def _submission():
    return {
        "run_id": "r1",
        "agent_version": "a1",
        "checkpoint_key": "c1",
        "deadline_at": "2099-01-01T00:00:00Z",
        "snapshot_id": "snapshot-1",
        "snapshot_digest": "a" * 64,
        "gateway_url": "http://api:8000/internal/runner",
        "run_token": "secret-token",
    }
