import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

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

    def health_check(self, *, monotonic_deadline=None):
        return {"status": "healthy" if self.health else "unhealthy", "sandbox": self.health}

    def submit(self, payload, *, monotonic_deadline=None):
        self.requests.append(payload)
        return {"run_id": payload["run_id"], "status": "accepted"}

    def status(self, run_id, *, monotonic_deadline=None):
        return {"run_id": run_id, "status": "exited", "exit_code": 0, "oom_killed": False}

    def terminate(self, run_id, *, monotonic_deadline=None):
        return {"run_id": run_id, "status": "terminated"}

    def cleanup(self, run_id, *, monotonic_deadline=None):
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
            execution_deadline_at="2098-12-31T23:59:00Z",
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
        execution_deadline_at="2098-12-31T23:59:00Z",
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
        "execution_deadline_at": "2098-12-31T23:59:00Z",
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
        "execution_deadline_at": "2098-12-31T23:59:00Z",
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
        "execution_deadline_at": "2098-12-31T23:59:00Z",
    }
    assert [(item[0], item[1]) for item in observed[2:]] == [
        ("GET", "http://runner:8090/runs/run-1"),
        ("POST", "http://runner:8090/runs/run-1/terminate"),
        ("DELETE", "http://runner:8090/runs/run-1"),
    ]


def test_workflow_runner_submit_enforces_absolute_slow_drip_deadline():
    received_deadlines = []

    class SlowDripHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_GET(self):
            self._write_json(b'{"status":"healthy","sandbox":true}')

        def do_POST(self):
            received_deadlines.append(
                self.headers.get("X-Request-Deadline-At")
            )
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self._write_json(b'{"status":"accepted"}', slow=True)

        def _write_json(self, body, *, slow=False):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                for byte in body:
                    self.wfile.write(bytes((byte,)))
                    self.wfile.flush()
                    if slow:
                        time.sleep(0.04)
            except OSError:
                return

        def log_message(self, _format, *_args):
            return

    class TestServer(ThreadingHTTPServer):
        daemon_threads = True

    server = TestServer(("127.0.0.1", 0), SlowDripHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = WorkflowRunnerClient(
        WorkflowRunnerHttpTransport(
            f"http://127.0.0.1:{server.server_port}"
        )
    )
    started_at = time.monotonic()
    operation_elapsed = None
    try:
        with pytest.raises(RunnerUnavailableError):
            client.submit(
                "run-1",
                "agent-v1",
                "runtime",
                snapshot_id="snapshot-1",
                snapshot_digest="a" * 64,
                gateway_url="http://api:8000/internal/runner",
                run_token="secret-token",
                deadline_at="2099-01-01T00:00:00Z",
                execution_deadline_at="2098-12-31T23:59:00Z",
                monotonic_deadline=started_at + 0.2,
            )
        operation_elapsed = time.monotonic() - started_at
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert operation_elapsed is not None and operation_elapsed < 0.5
    assert received_deadlines and received_deadlines[0] is not None


def test_workflow_runner_client_factory_requires_explicit_enablement(monkeypatch):
    monkeypatch.delenv("IAP_WORKFLOW_RUNNER_URL", raising=False)
    monkeypatch.delenv("IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED", raising=False)
    assert workflow_runner_client_from_env() is None

    monkeypatch.setenv("IAP_WORKFLOW_RUNNER_URL", "http://workflow-runner:8090")
    monkeypatch.setenv("IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED", "true")
    assert workflow_runner_client_from_env().transport.base_url == "http://workflow-runner:8090"
