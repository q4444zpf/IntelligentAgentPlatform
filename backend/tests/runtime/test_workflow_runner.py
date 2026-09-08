import asyncio
import json
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from datetime import datetime

import httpx
import anyio.to_thread
import pytest

from app.runtime.workflow_runner import (
    RunnerDeadlineExceededError,
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


@pytest.fixture
def workflow_http_server():
    observed = []
    responses = {
        "/health": (200, b'{"status":"healthy","sandbox":true}'),
        "/runs": (200, b'{"status":"accepted"}'),
    }
    delays = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            observed.append((self.path, dict(self.headers), body))
            time.sleep(delays.get(self.path, 0))
            if responses[self.path] is None:
                self.close_connection = True
                return
            status, response_body = responses[self.path]
            self.send_response(status)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            try:
                self.wfile.write(response_body)
            except OSError:
                pass

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", observed, responses, delays
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def workflow_lifecycle(monkeypatch):
    from app.runtime import workflow_runner as module

    contexts, loops, clients, requests, runs = [], [], [], [], []
    real_create_context = module.create_runtime_ssl_context
    real_client = httpx.AsyncClient
    real_run = asyncio.run

    async def create_context():
        loops.append(asyncio.get_running_loop())
        context = await real_create_context()
        contexts.append(context)
        return context

    class ObservedClient(real_client):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            clients.append(self)

        async def request(self, *args, **kwargs):
            loops.append(asyncio.get_running_loop())
            requests.append((kwargs.get("timeout", self.timeout), time.monotonic()))
            return await super().request(*args, **kwargs)

    def run(coro):
        runs.append(coro)
        return real_run(coro)

    monkeypatch.setattr(module, "create_runtime_ssl_context", create_context)
    monkeypatch.setattr(httpx, "AsyncClient", ObservedClient)
    monkeypatch.setattr(asyncio, "run", run)
    return contexts, loops, clients, requests, runs


def submit_workflow(client, *, monotonic_deadline=None):
    return client.submit(
        "run-1", "agent-v1", "runtime",
        snapshot_id="snapshot-1",
        snapshot_digest="a" * 64,
        gateway_url="http://api:8000/internal/runner",
        run_token="secret-token",
        deadline_at="2099-01-01T00:00:00Z",
        execution_deadline_at="2098-12-31T23:59:00Z",
        monotonic_deadline=monotonic_deadline,
    )


def test_workflow_submit_owns_one_http_lifecycle(workflow_http_server, workflow_lifecycle):
    url, observed, _, delays = workflow_http_server
    delays["/health"] = 0.08
    contexts, loops, clients, requests, runs = workflow_lifecycle
    deadline = time.monotonic() + 5
    client = WorkflowRunnerClient(WorkflowRunnerHttpTransport(url))

    assert submit_workflow(client, monotonic_deadline=deadline) == {"status": "accepted"}

    assert [item[0] for item in observed] == ["/health", "/runs"]
    assert json.loads(observed[1][2]) == {
        "run_id": "run-1", "agent_version": "agent-v1", "checkpoint_key": "runtime",
        "snapshot_id": "snapshot-1", "snapshot_digest": "a" * 64,
        "gateway_url": "http://api:8000/internal/runner", "run_token": "secret-token",
        "deadline_at": "2099-01-01T00:00:00Z",
        "execution_deadline_at": "2098-12-31T23:59:00Z",
    }
    assert len(contexts) == len(clients) == len(runs) == 1
    assert len({id(loop) for loop in loops}) == 1
    assert clients[0].is_closed
    deadlines = [datetime.fromisoformat(item[1]["X-Request-Deadline-At"]) for item in observed]
    assert abs((deadlines[1] - deadlines[0]).total_seconds()) < 0.05
    assert 0 < requests[1][0].read < requests[0][0].read <= 5


def test_workflow_submissions_isolate_tls_contexts(workflow_http_server, workflow_lifecycle):
    url, observed, _, _ = workflow_http_server
    contexts, loops, clients, _, runs = workflow_lifecycle
    client = WorkflowRunnerClient(WorkflowRunnerHttpTransport(url))
    assert submit_workflow(client) == submit_workflow(client) == {"status": "accepted"}
    assert [item[0] for item in observed] == ["/health", "/runs", "/health", "/runs"]
    assert len(contexts) == len(clients) == len(runs) == 2
    assert contexts[0] is not contexts[1]
    assert len({id(loop) for loop in loops}) == 2
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("path,status,body,error", [
    ("/health", 200, b'{"status":"unhealthy","sandbox":true}', RunnerUnavailableError),
    ("/health", 200, b'{"status":"healthy","sandbox":false}', RunnerUnavailableError),
    ("/health", 503, b'{}', RunnerUnavailableError),
    ("/health", 504, b'{}', RunnerDeadlineExceededError),
    ("/health", 200, b'not-json', RunnerUnavailableError),
    ("/health", 200, b'[]', RunnerUnavailableError),
    ("/runs", 503, b'{}', RunnerUnavailableError),
    ("/runs", 504, b'{}', RunnerDeadlineExceededError),
    ("/runs", 200, b'not-json', RunnerUnavailableError),
    ("/runs", 200, b'[]', RunnerUnavailableError),
])
def test_workflow_submit_closes_client_on_failure(
    workflow_http_server, workflow_lifecycle, path, status, body, error,
):
    url, observed, responses, _ = workflow_http_server
    responses[path] = (status, body)
    client = WorkflowRunnerClient(WorkflowRunnerHttpTransport(url))
    with pytest.raises(error) as captured:
        submit_workflow(client, monotonic_deadline=time.monotonic() + 5)
    assert type(captured.value) is error
    assert [item[0] for item in observed] == (["/health"] if path == "/health" else ["/health", "/runs"])
    assert len(workflow_lifecycle[2]) == 1
    assert workflow_lifecycle[2][0].is_closed


@pytest.mark.parametrize("path", ["/health", "/runs"])
def test_workflow_budget_expiry_closes_client(workflow_http_server, workflow_lifecycle, path):
    url, observed, _, delays = workflow_http_server
    delays[path] = 0.5
    client = WorkflowRunnerClient(WorkflowRunnerHttpTransport(url))
    with pytest.raises(RunnerDeadlineExceededError):
        submit_workflow(client, monotonic_deadline=time.monotonic() + 0.3)
    assert [item[0] for item in observed] == (["/health"] if path == "/health" else ["/health", "/runs"])
    assert len(workflow_lifecycle[2]) == 1
    assert workflow_lifecycle[2][0].is_closed


@pytest.mark.parametrize("path", ["/health", "/runs"])
def test_workflow_connection_failure_closes_client(workflow_http_server, workflow_lifecycle, path):
    url, observed, responses, _ = workflow_http_server
    responses[path] = None
    with pytest.raises(RunnerUnavailableError) as captured:
        submit_workflow(WorkflowRunnerClient(WorkflowRunnerHttpTransport(url)))
    assert type(captured.value) is RunnerUnavailableError
    assert [item[0] for item in observed] == (["/health"] if path == "/health" else ["/health", "/runs"])
    assert len(workflow_lifecycle[2]) == 1
    assert workflow_lifecycle[2][0].is_closed


def test_workflow_default_budget_restarts_for_submit(workflow_http_server, workflow_lifecycle, monkeypatch):
    from app.runtime import workflow_runner as module

    url, observed, _, delays = workflow_http_server
    # Scale both default phases to exercise the boundary without a 20 second test.
    monkeypatch.setattr(module, "_DEFAULT_REQUEST_TIMEOUT_SECONDS", 0.8)
    delays.update({"/health": 0.45, "/runs": 0.45})
    client = WorkflowRunnerClient(WorkflowRunnerHttpTransport(url))
    assert submit_workflow(client) == {"status": "accepted"}
    assert [item[0] for item in observed] == ["/health", "/runs"]
    assert all("X-Request-Deadline-At" not in item[1] for item in observed)
    assert len(workflow_lifecycle[2]) == 1
    assert workflow_lifecycle[2][0].is_closed


def test_workflow_expired_submit_does_not_initialize_or_send(workflow_http_server, workflow_lifecycle):
    url, observed, _, _ = workflow_http_server
    with pytest.raises(RunnerDeadlineExceededError):
        submit_workflow(WorkflowRunnerClient(WorkflowRunnerHttpTransport(url)), monotonic_deadline=time.monotonic() - 1)
    assert observed == []
    assert workflow_lifecycle[0] == workflow_lifecycle[2] == []


@pytest.mark.parametrize("error", [TimeoutError("early initialization failure"), httpx.ConnectTimeout("early connect failure")])
def test_workflow_early_dependency_timeout_remains_unavailable(workflow_http_server, monkeypatch, error):
    from app.runtime import workflow_runner as module

    url, observed, _, _ = workflow_http_server

    async def fail_initialization():
        raise error

    monkeypatch.setattr(module, "create_runtime_ssl_context", fail_initialization)
    with pytest.raises(RunnerUnavailableError) as captured:
        submit_workflow(WorkflowRunnerClient(WorkflowRunnerHttpTransport(url)), monotonic_deadline=time.monotonic() + 5)
    assert type(captured.value) is RunnerUnavailableError
    assert observed == []


def test_workflow_owned_timeout_is_deadline_expiry_before_next_clock_tick(workflow_http_server, monkeypatch):
    from app.runtime import workflow_runner as module

    url, observed, _, _ = workflow_http_server
    deadline = time.monotonic() + 5
    transport = WorkflowRunnerHttpTransport(url, monotonic=lambda: deadline - 0.05)

    async def slow_initialization():
        await asyncio.sleep(1)

    monkeypatch.setattr(module, "create_runtime_ssl_context", slow_initialization)
    with pytest.raises(RunnerDeadlineExceededError):
        submit_workflow(WorkflowRunnerClient(transport), monotonic_deadline=deadline)
    assert observed == []


def test_workflow_public_submit_cancels_initialization_without_deferred_request(workflow_http_server, monkeypatch):
    from app.runtime import http_tls

    url, observed, _, _ = workflow_http_server
    started, release, completed = threading.Event(), threading.Event(), threading.Event()
    real_build = http_tls._build_runtime_ssl_context

    def blocked_build():
        started.set()
        assert release.wait(3)
        try:
            return real_build()
        finally:
            completed.set()

    monkeypatch.setattr(http_tls, "_build_runtime_ssl_context", blocked_build)
    before = time.monotonic()
    try:
        with pytest.raises(RunnerDeadlineExceededError):
            submit_workflow(WorkflowRunnerClient(WorkflowRunnerHttpTransport(url)), monotonic_deadline=before + 0.1)
        assert started.is_set()
        assert time.monotonic() - before < 0.5
    finally:
        release.set()
        assert completed.wait(2)
    assert observed == []


@pytest.mark.parametrize("override", ["health_check", "submit", "is_healthy", "injected_request"])
def test_workflow_combined_submit_preserves_custom_behavior(override, monkeypatch):
    calls = []

    def request(method, url, *, headers, body=None):
        calls.append(method)
        return {"status": "healthy", "sandbox": True} if method == "GET" else {"status": "accepted"}

    transport = WorkflowRunnerHttpTransport("http://unused", request=request)
    client = WorkflowRunnerClient(transport)
    if override == "health_check":
        monkeypatch.setattr(transport, "health_check", lambda **kwargs: {"status": "unhealthy"})
    elif override == "submit":
        monkeypatch.setattr(transport, "submit", lambda *args, **kwargs: {"status": "custom"})
    elif override == "is_healthy":
        monkeypatch.setattr(client, "is_healthy", lambda **kwargs: False)

    if override in {"health_check", "is_healthy"}:
        with pytest.raises(RunnerUnavailableError):
            submit_workflow(client)
        assert calls == []
    else:
        expected = "custom" if override == "submit" else "accepted"
        assert submit_workflow(client) == {"status": expected}
        assert calls == (["GET"] if override == "submit" else ["GET", "POST"])


@pytest.mark.parametrize("override", ["health_check", "submit"])
def test_workflow_real_http_transport_overrides_are_not_bypassed(workflow_http_server, override, monkeypatch):
    url, observed, _, _ = workflow_http_server
    transport = WorkflowRunnerHttpTransport(url)
    if override == "health_check":
        monkeypatch.setattr(transport, override, lambda **kwargs: {"status": "unhealthy"})
        with pytest.raises(RunnerUnavailableError):
            submit_workflow(WorkflowRunnerClient(transport))
        assert observed == []
    else:
        monkeypatch.setattr(transport, override, lambda *args, **kwargs: {"status": "custom"})
        assert submit_workflow(WorkflowRunnerClient(transport)) == {"status": "custom"}
        assert [item[0] for item in observed] == ["/health"]


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


@pytest.mark.parametrize(
    "transport_error",
    [
        TimeoutError("Workflow Runner deadline expired"),
        httpx.ReadTimeout(
            "Workflow Runner deadline expired",
            request=httpx.Request("GET", "http://runner/runs/run-1"),
        ),
        httpx.HTTPStatusError(
            "Gateway Timeout",
            request=httpx.Request("GET", "http://runner/runs/run-1"),
            response=httpx.Response(
                504,
                request=httpx.Request("GET", "http://runner/runs/run-1"),
            ),
        ),
    ],
)
def test_runner_client_preserves_deadline_expiry_identity(transport_error):
    class DeadlineTransport(FakeTransport):
        def status(self, run_id, *, monotonic_deadline=None):
            raise transport_error

    client = WorkflowRunnerClient(DeadlineTransport())

    with pytest.raises(RunnerUnavailableError) as captured:
        client.status("run-1", monotonic_deadline=time.monotonic() - 1)

    assert isinstance(captured.value, RunnerDeadlineExceededError)


def test_runner_client_keeps_far_future_connect_timeout_as_unavailable():
    connect_timeout = httpx.ConnectTimeout(
        "connection timed out",
        request=httpx.Request("GET", "http://runner/runs/run-1"),
    )

    class UnavailableTransport(FakeTransport):
        def status(self, run_id, *, monotonic_deadline=None):
            raise connect_timeout

    client = WorkflowRunnerClient(UnavailableTransport())

    with pytest.raises(RunnerUnavailableError) as captured:
        client.status("run-1", monotonic_deadline=time.monotonic() + 300)

    assert type(captured.value) is RunnerUnavailableError


def test_runner_submit_preserves_deadline_expiry_from_health_check():
    class DeadlineTransport(FakeTransport):
        def health_check(self, *, monotonic_deadline=None):
            raise RunnerDeadlineExceededError("Workflow Runner deadline expired")

    client = WorkflowRunnerClient(DeadlineTransport())

    with pytest.raises(RunnerDeadlineExceededError):
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
            monotonic_deadline=time.monotonic() + 5,
        )


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


def test_workflow_runner_deadline_prevents_request_after_slow_tls_initialization(
    monkeypatch,
):
    from app.runtime import workflow_runner as workflow_runner_module

    started = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    received = threading.Event()

    def blocked_build():
        started.set()
        assert release.wait(3)
        try:
            return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        finally:
            completed.set()

    async def create_context():
        return await anyio.to_thread.run_sync(
            blocked_build, abandon_on_cancel=True
        )

    monkeypatch.setattr(
        workflow_runner_module,
        "create_runtime_ssl_context",
        create_context,
        raising=False,
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.set()
            body = b'{"status":"healthy","sandbox":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    transport = WorkflowRunnerHttpTransport(
        f"http://127.0.0.1:{server.server_port}"
    )
    try:
        with pytest.raises(TimeoutError):
            transport.health_check(
                monotonic_deadline=time.monotonic() + 0.05
            )
        assert started.is_set()
    finally:
        release.set()
        try:
            assert completed.wait(2)
            assert not received.wait(0.1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def test_workflow_runner_client_factory_requires_explicit_enablement(monkeypatch):
    monkeypatch.delenv("IAP_WORKFLOW_RUNNER_URL", raising=False)
    monkeypatch.delenv("IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED", raising=False)
    assert workflow_runner_client_from_env() is None

    monkeypatch.setenv("IAP_WORKFLOW_RUNNER_URL", "http://workflow-runner:8090")
    monkeypatch.setenv("IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED", "true")
    assert workflow_runner_client_from_env().transport.base_url == "http://workflow-runner:8090"
