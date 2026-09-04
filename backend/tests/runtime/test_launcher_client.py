import json
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx
import pytest

from app.runtime.launcher_client import (
    LauncherClient,
    LauncherClientError,
    LauncherDeadlineExceededError,
    LauncherHttpTransport,
)


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.inspect_status = "running"

    def create(self, run_id, workspace_path, execution, *, deadline_at=None):
        self.calls.append(("create", run_id, workspace_path, execution))
        return {"run_id": run_id, "status": "created"}

    def inspect(self, run_id, *, deadline_at=None):
        self.calls.append(("inspect", run_id))
        return {"run_id": run_id, "status": self.inspect_status}

    def cleanup(self, run_id, *, deadline_at=None):
        self.calls.append(("cleanup", run_id))
        return {"run_id": run_id, "status": "cleaned"}

    def terminate(self, run_id, *, deadline_at=None):
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
        execution_deadline_at="2098-12-31T23:59:00Z",
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
            "execution_deadline_at": "2098-12-31T23:59:00Z",
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
            execution_deadline_at="2098-12-31T23:59:00Z",
            snapshot_id="snapshot-1",
            snapshot_digest="a" * 64,
            gateway_url="http://api:8000/internal/runner",
            run_token="secret-token",
        )

    assert transport.calls[-1] == ("cleanup", "run-1")


def test_launcher_client_cleans_up_when_inspection_raises():
    class FailingInspectTransport(FakeTransport):
        def inspect(self, run_id, *, deadline_at=None):
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
            execution_deadline_at="2098-12-31T23:59:00Z",
            snapshot_id="snapshot-1",
            snapshot_digest="a" * 64,
            gateway_url="http://api:8000/internal/runner",
            run_token="secret-token",
        )

    assert transport.calls[-1] == ("cleanup", "run-1")


def test_launcher_client_compensation_keeps_original_execution_deadline():
    cleanup_deadlines = []

    class FailingInspectTransport(FakeTransport):
        def inspect(self, run_id, *, deadline_at=None):
            self.calls.append(("inspect", run_id))
            raise OSError("launcher connection lost")

        def cleanup(self, run_id, *, deadline_at=None):
            cleanup_deadlines.append(deadline_at)
            return super().cleanup(run_id, deadline_at=deadline_at)

    transport = FailingInspectTransport()
    client = LauncherClient(transport)
    execution_deadline_at = datetime.now(UTC) + timedelta(seconds=5)

    with pytest.raises(LauncherClientError):
        client.prepare(
            "run-1",
            agent_version="agent-v1",
            checkpoint_key="runtime",
            deadline_at="2099-01-01T00:00:00Z",
            execution_deadline_at=execution_deadline_at.isoformat(),
            snapshot_id="snapshot-1",
            snapshot_digest="a" * 64,
            gateway_url="http://api:8000/internal/runner",
            run_token="secret-token",
        )

    assert cleanup_deadlines == [execution_deadline_at]


def test_http_transport_sends_bearer_and_run_scope_headers():
    observed = {}
    request_deadline_at = datetime.now(UTC) + timedelta(seconds=5)

    def request(method, url, *, headers, body=None):
        observed.update(method=method, url=url, headers=headers, body=body)
        return {"run_id": "run-1", "status": "created"}

    transport = LauncherHttpTransport("http://launcher:8091", "secret", request=request)
    transport.create("run-1", "/workspace/run-1", {
        "agent_version": "agent-v1",
        "checkpoint_key": "runtime",
        "deadline_at": "2099-01-01T00:00:00Z",
        "execution_deadline_at": "2098-12-31T23:59:00Z",
        "snapshot_id": "snapshot-1",
        "snapshot_digest": "a" * 64,
        "gateway_url": "http://api:8000/internal/runner",
        "run_token": "secret-token",
    }, deadline_at=request_deadline_at)

    assert observed["method"] == "POST"
    assert observed["url"] == "http://launcher:8091/runs/run-1/container"
    assert observed["headers"] == {
        "Authorization": "Bearer secret",
        "X-Run-Id": "run-1",
        "Content-Type": "application/json",
        "X-Request-Deadline-At": request_deadline_at.isoformat(),
    }
    assert json.loads(observed["body"].decode()) == {
        "workspace_path": "/workspace/run-1",
        "agent_version": "agent-v1",
        "checkpoint_key": "runtime",
        "deadline_at": "2099-01-01T00:00:00Z",
        "execution_deadline_at": "2098-12-31T23:59:00Z",
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


@pytest.mark.parametrize(
    "transport_error",
    [
        TimeoutError("Sandbox Launcher deadline expired"),
        httpx.ReadTimeout(
            "Sandbox Launcher deadline expired",
            request=httpx.Request("GET", "http://launcher/runs/run-1/container"),
        ),
        httpx.HTTPStatusError(
            "Gateway Timeout",
            request=httpx.Request("GET", "http://launcher/runs/run-1/container"),
            response=httpx.Response(
                504,
                request=httpx.Request(
                    "GET", "http://launcher/runs/run-1/container"
                ),
            ),
        ),
    ],
)
def test_launcher_client_preserves_deadline_expiry_identity(transport_error):
    class DeadlineTransport(FakeTransport):
        def inspect(self, run_id, *, deadline_at=None):
            raise transport_error

    client = LauncherClient(DeadlineTransport())

    with pytest.raises(LauncherClientError) as captured:
        client.inspect(
            "run-1",
            request_deadline_at="2000-01-01T00:00:00Z",
        )

    assert isinstance(captured.value, LauncherDeadlineExceededError)


def test_launcher_client_keeps_far_future_connect_timeout_as_unavailable():
    connect_timeout = httpx.ConnectTimeout(
        "connection timed out",
        request=httpx.Request("GET", "http://launcher/runs/run-1/container"),
    )

    class UnavailableTransport(FakeTransport):
        def inspect(self, run_id, *, deadline_at=None):
            raise connect_timeout

    client = LauncherClient(UnavailableTransport())

    with pytest.raises(LauncherClientError) as captured:
        client.inspect(
            "run-1",
            request_deadline_at="2099-01-01T00:00:00Z",
        )

    assert type(captured.value) is LauncherClientError


def test_launcher_prepare_preserves_deadline_expiry_from_inspection():
    class DeadlineTransport(FakeTransport):
        def inspect(self, run_id, *, deadline_at=None):
            raise LauncherDeadlineExceededError(
                "sandbox execution deadline expired"
            )

    client = LauncherClient(DeadlineTransport())

    with pytest.raises(LauncherDeadlineExceededError):
        client.prepare(
            "run-1",
            agent_version="agent-v1",
            checkpoint_key="runtime",
            deadline_at="2099-01-01T00:00:00Z",
            execution_deadline_at="2098-12-31T23:59:00Z",
            snapshot_id="snapshot-1",
            snapshot_digest="a" * 64,
            gateway_url="http://api:8000/internal/runner",
            run_token="secret-token",
        )


@pytest.mark.parametrize("failure_point", ["create", "inspect"])
@pytest.mark.parametrize(
    "transport_error",
    [
        TimeoutError("Sandbox Launcher deadline expired"),
        httpx.ReadTimeout(
            "Sandbox Launcher deadline expired",
            request=httpx.Request("POST", "http://launcher/runs/run-1/container"),
        ),
        httpx.HTTPStatusError(
            "Gateway Timeout",
            request=httpx.Request("POST", "http://launcher/runs/run-1/container"),
            response=httpx.Response(
                504,
                request=httpx.Request(
                    "POST", "http://launcher/runs/run-1/container"
                ),
            ),
        ),
    ],
)
def test_launcher_prepare_translates_transport_deadline_expiry(
    failure_point,
    transport_error,
    monkeypatch,
):
    from app.runtime import launcher_client as launcher_client_module

    execution_deadline = datetime(2098, 12, 31, 23, 59, tzinfo=UTC)
    current_time = [execution_deadline - timedelta(seconds=1)]

    class MutableDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return current_time[0]

    monkeypatch.setattr(launcher_client_module, "datetime", MutableDatetime)

    class DeadlineTransport(FakeTransport):
        def create(self, run_id, workspace_path, execution, *, deadline_at=None):
            if failure_point == "create":
                current_time[0] = execution_deadline
                raise transport_error
            return super().create(
                run_id,
                workspace_path,
                execution,
                deadline_at=deadline_at,
            )

        def inspect(self, run_id, *, deadline_at=None):
            if failure_point == "inspect":
                current_time[0] = execution_deadline
                raise transport_error
            return super().inspect(run_id, deadline_at=deadline_at)

    client = LauncherClient(DeadlineTransport())

    with pytest.raises(LauncherDeadlineExceededError):
        client.prepare(
            "run-1",
            agent_version="agent-v1",
            checkpoint_key="runtime",
            deadline_at="2099-01-01T00:00:00Z",
            execution_deadline_at="2098-12-31T23:59:00Z",
            snapshot_id="snapshot-1",
            snapshot_digest="a" * 64,
            gateway_url="http://api:8000/internal/runner",
            run_token="secret-token",
        )


def test_launcher_prepare_enforces_execution_deadline_during_slow_response():
    class SlowDripHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self._write_json(b'{"run_id":"run-1","status":"created"}', slow=True)

        def do_GET(self):
            self._write_json(b'{"run_id":"run-1","status":"running"}')

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
                        time.sleep(0.03)
            except OSError:
                return

        def log_message(self, _format, *_args):
            return

    class TestServer(ThreadingHTTPServer):
        daemon_threads = True

    server = TestServer(("127.0.0.1", 0), SlowDripHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = LauncherClient(
        LauncherHttpTransport(
            f"http://127.0.0.1:{server.server_port}",
            "secret",
        )
    )
    started_at = time.monotonic()
    execution_deadline_at = datetime.now(UTC) + timedelta(seconds=0.2)
    operation_elapsed = None
    try:
        with pytest.raises(LauncherClientError):
            client.prepare(
                "run-1",
                agent_version="agent-v1",
                checkpoint_key="runtime",
                deadline_at="2099-01-01T00:00:00Z",
                execution_deadline_at=execution_deadline_at.isoformat(),
                snapshot_id="snapshot-1",
                snapshot_digest="a" * 64,
                gateway_url="http://api:8000/internal/runner",
                run_token="secret-token",
            )
        operation_elapsed = time.monotonic() - started_at
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert operation_elapsed is not None and operation_elapsed < 0.5
