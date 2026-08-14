from fastapi.testclient import TestClient

from app.runtime.container_launcher import LauncherUnavailableError
from app.runtime.container_policy import InvalidContainerPolicyError
from app.runtime.launcher_api import create_launcher_app


class FakeLauncher:
    def __init__(self):
        self.calls = []

    def create(self, run_id: str, payload: dict):
        self.calls.append(("create", run_id, payload))
        return {"run_id": run_id, "container_id": "c-1", "status": "created"}

    def inspect(self, run_id: str):
        self.calls.append(("inspect", run_id))
        return {"run_id": run_id, "container_id": "c-1", "status": "running"}

    def terminate(self, run_id: str):
        self.calls.append(("terminate", run_id))
        return {"run_id": run_id, "status": "terminated"}

    def cleanup(self, run_id: str):
        self.calls.append(("cleanup", run_id))
        return {"run_id": run_id, "status": "cleaned"}


def execution_request(workspace_path="/workspace/run-1"):
    return {
        "workspace_path": workspace_path,
        "agent_version": "agent-v1",
        "checkpoint_key": "runtime",
        "deadline_at": "2099-01-01T00:00:00Z",
        "snapshot_id": "snapshot-1",
        "snapshot_digest": "a" * 64,
        "gateway_url": "http://api:8000/internal/runner",
        "run_token": "run-secret-token",
    }


def test_launcher_rejects_missing_or_invalid_runner_token():
    client = TestClient(create_launcher_app(FakeLauncher(), runner_token="secret"))

    assert client.get("/health").status_code == 401
    assert client.get("/health", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_launcher_rejects_empty_configured_token():
    client = TestClient(create_launcher_app(FakeLauncher(), runner_token=""))
    assert client.get("/health").status_code == 503


def test_launcher_scopes_lifecycle_operations_to_authenticated_run():
    launcher = FakeLauncher()
    client = TestClient(create_launcher_app(launcher, runner_token="secret"))
    headers = {"Authorization": "Bearer secret", "X-Run-Id": "run-1"}

    assert client.post(
        "/runs/run-1/container",
        headers=headers,
        json=execution_request(),
    ).status_code == 200
    assert client.get("/runs/run-1/container", headers=headers).json()["status"] == "running"
    assert client.post("/runs/run-1/container/terminate", headers=headers).json()["status"] == "terminated"
    assert client.delete("/runs/run-1/container", headers=headers).json()["status"] == "cleaned"
    assert all(call[1] == "run-1" for call in launcher.calls)

    forbidden = client.get("/runs/run-2/container", headers=headers)
    assert forbidden.status_code == 403


def test_launcher_requires_run_scoped_header_for_create():
    client = TestClient(create_launcher_app(FakeLauncher(), runner_token="secret"))
    response = client.post(
        "/runs/run-1/container",
        headers={"Authorization": "Bearer secret"},
        json=execution_request(),
    )
    assert response.status_code == 403


def test_launcher_maps_readiness_failure_to_service_unavailable():
    class UnsafeLauncher(FakeLauncher):
        def create(self, run_id, payload):
            raise LauncherUnavailableError("sandbox readiness check failed")

    client = TestClient(create_launcher_app(UnsafeLauncher(), runner_token="secret"))
    response = client.post(
        "/runs/run-1/container",
        headers={"Authorization": "Bearer secret", "X-Run-Id": "run-1"},
        json=execution_request(),
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "sandbox readiness check failed"}


def test_launcher_returns_safe_not_found_for_unregistered_run():
    class MissingRunLauncher(FakeLauncher):
        def inspect(self, run_id):
            raise LauncherUnavailableError("container is not registered for run")

    client = TestClient(create_launcher_app(MissingRunLauncher(), runner_token="secret"))
    response = client.get(
        "/runs/missing-run/container",
        headers={"Authorization": "Bearer secret", "X-Run-Id": "missing-run"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Run container was not found"}


def test_launcher_returns_safe_validation_error_for_invalid_workspace():
    class InvalidWorkspaceLauncher(FakeLauncher):
        def create(self, run_id, payload):
            raise InvalidContainerPolicyError("workspace path must be an absolute run directory")

    client = TestClient(create_launcher_app(InvalidWorkspaceLauncher(), runner_token="secret"))
    response = client.post(
        "/runs/run-1/container",
        headers={"Authorization": "Bearer secret", "X-Run-Id": "run-1"},
        json=execution_request("/private/internal/path"),
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid workspace path"}
    assert "/private/internal/path" not in response.text


def test_launcher_preserves_complete_execution_identity_for_container_creation():
    launcher = FakeLauncher()
    client = TestClient(create_launcher_app(launcher, runner_token="secret"))
    request = {
        "workspace_path": "/workspace/run-1",
        "agent_version": "agent-v1",
        "checkpoint_key": "runtime",
        "deadline_at": "2099-01-01T00:00:00Z",
        "snapshot_id": "snapshot-1",
        "snapshot_digest": "a" * 64,
        "gateway_url": "http://api:8000/internal/runner",
        "run_token": "run-secret-token",
    }

    response = client.post(
        "/runs/run-1/container",
        headers={"Authorization": "Bearer secret", "X-Run-Id": "run-1"},
        json=request,
    )

    assert response.status_code == 200
    assert launcher.calls == [("create", "run-1", request)]
