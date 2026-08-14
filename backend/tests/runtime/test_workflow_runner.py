import json

import pytest

from app.runtime.workflow_runner import (
    RunnerUnavailableError,
    WorkflowRunnerClient,
    WorkflowRunnerHttpTransport,
    workflow_runner_client_from_env,
)


class FakeTransport:
    def __init__(self, health=True):
        self.health = health
        self.requests = []

    def health_check(self):
        return {"status": "healthy" if self.health else "unhealthy", "sandbox": self.health}

    def submit(self, payload):
        self.requests.append(payload)
        return {"run_id": payload["run_id"], "status": "accepted"}

    def status(self, run_id):
        return {"run_id": run_id, "status": "exited", "exit_code": 0, "oom_killed": False}

    def terminate(self, run_id):
        return {"run_id": run_id, "status": "terminated"}

    def cleanup(self, run_id):
        return {"run_id": run_id, "status": "cleaned"}


def test_runner_client_requires_healthy_sandbox_before_submit():
    transport = FakeTransport(health=False)
    client = WorkflowRunnerClient(transport)

    with pytest.raises(RunnerUnavailableError, match="Workflow Runner is unavailable"):
        client.submit(
            "run-1",
            "agent-v1",
            "checkpoint-1",
            snapshot_id="snapshot-1",
            snapshot_digest="a" * 64,
            gateway_url="http://api:8000/internal/runner",
            run_token="secret-token",
            deadline_at="2099-01-01T00:00:00Z",
        )
    assert transport.requests == []


def test_runner_client_submits_only_run_snapshot_references():
    transport = FakeTransport()
    client = WorkflowRunnerClient(transport)

    response = client.submit(
        "run-1",
        "agent-v1",
        "checkpoint-1",
        snapshot_id="snapshot-1",
        snapshot_digest="a" * 64,
        gateway_url="http://api:8000/internal/runner",
        run_token="secret-token",
        deadline_at="2099-01-01T00:00:00Z",
    )

    assert response == {"run_id": "run-1", "status": "accepted"}
    assert transport.requests == [{
        "run_id": "run-1",
        "agent_version": "agent-v1",
        "checkpoint_key": "checkpoint-1",
        "snapshot_id": "snapshot-1",
        "snapshot_digest": "a" * 64,
        "gateway_url": "http://api:8000/internal/runner",
        "run_token": "secret-token",
        "deadline_at": "2099-01-01T00:00:00Z",
    }]


def test_runner_client_exposes_run_lifecycle_operations():
    client = WorkflowRunnerClient(FakeTransport())

    assert client.status("run-1")["exit_code"] == 0
    assert client.terminate("run-1")["status"] == "terminated"
    assert client.cleanup("run-1")["status"] == "cleaned"


def test_workflow_runner_http_transport_uses_json_lifecycle_contract():
    observed = []

    def request(method, url, *, headers, body=None):
        observed.append((method, url, headers, body))
        return {"status": "healthy", "sandbox": True} if url.endswith("/health") else {"status": "accepted"}

    transport = WorkflowRunnerHttpTransport("http://runner:8090", request=request)
    assert transport.health_check()["sandbox"] is True
    transport.submit({
        "run_id": "run-1",
        "agent_version": "agent-v1",
        "checkpoint_key": "runtime",
        "snapshot_id": "snapshot-1",
        "snapshot_digest": "a" * 64,
        "gateway_url": "http://api:8000/internal/runner",
        "run_token": "secret-token",
        "deadline_at": "2099-01-01T00:00:00Z",
    })
    transport.status("run-1")
    transport.terminate("run-1")
    transport.cleanup("run-1")

    assert observed[1][0:3] == ("POST", "http://runner:8090/runs", {"Content-Type": "application/json"})
    assert json.loads(observed[1][3].decode()) == {
        "run_id": "run-1",
        "agent_version": "agent-v1",
        "checkpoint_key": "runtime",
        "snapshot_id": "snapshot-1",
        "snapshot_digest": "a" * 64,
        "gateway_url": "http://api:8000/internal/runner",
        "run_token": "secret-token",
        "deadline_at": "2099-01-01T00:00:00Z",
    }
    assert [(item[0], item[1]) for item in observed[2:]] == [
        ("GET", "http://runner:8090/runs/run-1"),
        ("POST", "http://runner:8090/runs/run-1/terminate"),
        ("DELETE", "http://runner:8090/runs/run-1"),
    ]


def test_workflow_runner_client_factory_requires_explicit_enablement(monkeypatch):
    monkeypatch.delenv("IAP_WORKFLOW_RUNNER_URL", raising=False)
    monkeypatch.delenv("IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED", raising=False)
    assert workflow_runner_client_from_env() is None

    monkeypatch.setenv("IAP_WORKFLOW_RUNNER_URL", "http://workflow-runner:8090")
    monkeypatch.setenv("IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED", "true")
    assert workflow_runner_client_from_env().transport.base_url == "http://workflow-runner:8090"
