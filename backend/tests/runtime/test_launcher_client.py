import json

import pytest

from app.runtime.launcher_client import (
    LauncherClient,
    LauncherClientError,
    LauncherHttpTransport,
)


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.inspect_status = "running"

    def create(self, run_id, workspace_path, execution):
        self.calls.append(("create", run_id, workspace_path, execution))
        return {"run_id": run_id, "status": "created"}

    def inspect(self, run_id):
        self.calls.append(("inspect", run_id))
        return {"run_id": run_id, "status": self.inspect_status}

    def cleanup(self, run_id):
        self.calls.append(("cleanup", run_id))
        return {"run_id": run_id, "status": "cleaned"}

    def terminate(self, run_id):
        self.calls.append(("terminate", run_id))
        return {"run_id": run_id, "status": "terminated"}


def test_launcher_client_creates_and_inspects_current_run():
    transport = FakeTransport()
    client = LauncherClient(transport)

    result = client.prepare(
        "run-1",
        agent_version="agent-v1",
        checkpoint_key="runtime",
        deadline_at="2099-01-01T00:00:00Z",
        snapshot_id="snapshot-1",
        snapshot_digest="a" * 64,
        gateway_url="http://api:8000/internal/runner",
        run_token="secret-token",
    )

    assert result == {"run_id": "run-1", "status": "running"}
    assert transport.calls == [
        ("create", "run-1", "/workspace/run-1", {
            "agent_version": "agent-v1",
            "checkpoint_key": "runtime",
            "deadline_at": "2099-01-01T00:00:00Z",
            "snapshot_id": "snapshot-1",
            "snapshot_digest": "a" * 64,
            "gateway_url": "http://api:8000/internal/runner",
            "run_token": "secret-token",
        }),
        ("inspect", "run-1"),
    ]


def test_launcher_client_cleans_up_when_inspection_is_not_running():
    transport = FakeTransport()
    transport.inspect_status = "exited"
    client = LauncherClient(transport)

    with pytest.raises(LauncherClientError, match="not running"):
        client.prepare(
            "run-1",
            agent_version="agent-v1",
            checkpoint_key="runtime",
            deadline_at="2099-01-01T00:00:00Z",
            snapshot_id="snapshot-1",
            snapshot_digest="a" * 64,
            gateway_url="http://api:8000/internal/runner",
            run_token="secret-token",
        )

    assert transport.calls[-1] == ("cleanup", "run-1")


def test_launcher_client_cleans_up_when_inspection_raises():
    class FailingInspectTransport(FakeTransport):
        def inspect(self, run_id):
            self.calls.append(("inspect", run_id))
            raise OSError("launcher connection lost")

    transport = FailingInspectTransport()
    client = LauncherClient(transport)

    with pytest.raises(LauncherClientError, match="unavailable"):
        client.prepare(
            "run-1",
            agent_version="agent-v1",
            checkpoint_key="runtime",
            deadline_at="2099-01-01T00:00:00Z",
            snapshot_id="snapshot-1",
            snapshot_digest="a" * 64,
            gateway_url="http://api:8000/internal/runner",
            run_token="secret-token",
        )

    assert transport.calls[-1] == ("cleanup", "run-1")


def test_http_transport_sends_bearer_and_run_scope_headers():
    observed = {}

    def request(method, url, *, headers, body=None):
        observed.update(method=method, url=url, headers=headers, body=body)
        return {"run_id": "run-1", "status": "created"}

    transport = LauncherHttpTransport("http://launcher:8091", "secret", request=request)
    transport.create("run-1", "/workspace/run-1", {
        "agent_version": "agent-v1",
        "checkpoint_key": "runtime",
        "deadline_at": "2099-01-01T00:00:00Z",
        "snapshot_id": "snapshot-1",
        "snapshot_digest": "a" * 64,
        "gateway_url": "http://api:8000/internal/runner",
        "run_token": "secret-token",
    })

    assert observed["method"] == "POST"
    assert observed["url"] == "http://launcher:8091/runs/run-1/container"
    assert observed["headers"] == {
        "Authorization": "Bearer secret",
        "X-Run-Id": "run-1",
        "Content-Type": "application/json",
    }
    assert json.loads(observed["body"].decode()) == {
        "workspace_path": "/workspace/run-1",
        "agent_version": "agent-v1",
        "checkpoint_key": "runtime",
        "deadline_at": "2099-01-01T00:00:00Z",
        "snapshot_id": "snapshot-1",
        "snapshot_digest": "a" * 64,
        "gateway_url": "http://api:8000/internal/runner",
        "run_token": "secret-token",
    }


def test_launcher_client_exposes_sanitized_status_and_lifecycle_operations():
    transport = FakeTransport()
    client = LauncherClient(transport)

    assert client.inspect("run-1") == {"run_id": "run-1", "status": "running"}
    assert client.terminate("run-1")["status"] == "terminated"
    assert client.cleanup("run-1")["status"] == "cleaned"
