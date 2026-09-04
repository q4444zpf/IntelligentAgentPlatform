import json
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from types import SimpleNamespace

import httpx
import pytest

from app.runtime.execution_contract import RunExecutionRequest
from app.runtime.runner_gateway_client import (
    RunnerGatewayBusinessError,
    RunnerGatewayClient,
    RunnerGatewayDeadlineExceeded,
    RunnerGatewayResponseInvalid,
    RunnerGatewayUnavailable,
)


def _request():
    deadline = datetime.now(timezone.utc) + timedelta(minutes=5)
    return RunExecutionRequest(
        run_id="run-1",
        agent_version="agent-v1",
        checkpoint_key="checkpoint-1",
        deadline_at=deadline,
        execution_deadline_at=deadline,
        snapshot_id="snapshot-1",
        snapshot_digest="a" * 64,
        gateway_url="http://api:8000/internal/runner",
        run_token="secret-token",
    )


def test_client_from_execution_request_sends_scoped_headers():
    captured = {}

    def handler(request):
        captured["headers"] = dict(request.headers)
        captured["url"] = str(request.url)
        captured["timeout"] = dict(request.extensions["timeout"])
        return httpx.Response(
            200,
            json={
                "snapshot_id": "snapshot-1",
                "run_id": "run-1",
                "digest": "a" * 64,
                "payload": {
                    "snapshot_id": "snapshot-1",
                    "run_id": "run-1",
                    "unit_id": "unit-1",
                    "project_id": "project-1",
                    "user_id": "user-1",
                    "actor": {
                        "id": "agent-1",
                        "name": "Agent",
                        "description": "",
                        "runtime_form": "common",
                        "language": "zh-CN",
                        "system_prompt": "",
                        "context_prompt": "",
                        "approval_policy": "never",
                    },
                    "model": {"provider_id": "provider-1", "model": "model-1"},
                    "messages": [],
                    "limits": {"snapshot_max_bytes": 1048576},
                    "created_at": "2026-08-14T10:00:00Z",
                },
            },
        )

    client = RunnerGatewayClient.from_execution_request(
        _request(), transport=httpx.MockTransport(handler)
    )

    snapshot = client.get_snapshot()

    assert snapshot.digest == "a" * 64
    assert captured["url"].endswith("/runs/run-1/snapshot")
    assert captured["headers"]["authorization"] == "Bearer secret-token"
    assert captured["headers"]["x-run-id"] == "run-1"
    assert captured["headers"]["x-snapshot-digest"] == "a" * 64
    assert captured["timeout"]["connect"] <= 3.0
    assert captured["timeout"]["pool"] <= 3.0
    assert captured["timeout"]["read"] <= 90.0
    assert captured["timeout"]["write"] <= 90.0
    assert "secret-token" not in repr(client)


def test_mutating_request_sends_idempotency_key_and_validates_response():
    captured = {}

    def handler(request):
        captured["idempotency"] = request.headers["Idempotency-Key"]
        return httpx.Response(
            200,
            json={
                "content": "ok",
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
                "tool_calls": [],
            },
        )

    client = RunnerGatewayClient.from_execution_request(
        _request(), transport=httpx.MockTransport(handler)
    )

    response = client.invoke_model(
        {
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [],
            "invocation_sequence": 0,
        },
        "model-0",
    )

    assert response["content"] == "ok"
    assert captured["idempotency"] == "model-0"


def test_client_maps_timeout_without_exposing_token():
    def handler(request):
        raise httpx.ReadTimeout("secret-token upstream timeout", request=request)

    client = RunnerGatewayClient.from_execution_request(
        _request(), transport=httpx.MockTransport(handler)
    )

    with pytest.raises(RunnerGatewayUnavailable) as captured:
        client.get_snapshot()

    assert captured.value.code == "runner_gateway_unavailable"
    assert "secret-token" not in str(captured.value)


def test_execution_absolute_guard_timeout_is_authoritative_before_next_clock_tick(
    monkeypatch,
):
    async def expire_at_absolute_guard(*_args, **_kwargs):
        raise TimeoutError("Runner Gateway deadline expired")

    ticks = iter([100.0, 100.999])
    monkeypatch.setattr(
        RunnerGatewayClient,
        "_remaining_seconds",
        staticmethod(lambda _deadline_at: 1.0),
    )
    monkeypatch.setattr(
        RunnerGatewayClient,
        "_async_request_content",
        staticmethod(expire_at_absolute_guard),
    )
    monkeypatch.setattr(
        "app.runtime.runner_gateway_client.time",
        SimpleNamespace(monotonic=ticks.__next__),
    )
    client = RunnerGatewayClient.from_execution_request(_request())

    with pytest.raises(RunnerGatewayDeadlineExceeded) as captured:
        client.get_snapshot()

    assert captured.value.code == "sandbox_timeout"


def test_client_maps_business_error_without_returning_server_message():
    def handler(_request):
        return httpx.Response(
            409,
            json={
                "code": "tool_approval_required",
                "message": "internal path C:/secret",
                "approval_id": "approval-1",
            },
        )

    client = RunnerGatewayClient.from_execution_request(
        _request(), transport=httpx.MockTransport(handler)
    )

    with pytest.raises(RunnerGatewayBusinessError) as captured:
        client.invoke_tool(
            tool_id="water.query",
            version="1",
            tool_call_id="call-1",
            arguments={},
            invocation_sequence=0,
            idempotency_key="tool-call-1",
        )

    assert captured.value.code == "tool_approval_required"
    assert captured.value.details == {"approval_id": "approval-1"}
    assert "C:/secret" not in str(captured.value)


def test_team_tool_invocation_forwards_member_agent_identity():
    captured = {}

    def handler(request):
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"invocation_id": "invocation-1", "value": {"ok": True}},
        )

    client = RunnerGatewayClient.from_execution_request(
        _request(), transport=httpx.MockTransport(handler)
    )

    response = client.invoke_tool(
        tool_id="water.query",
        version="1",
        tool_call_id="call-1",
        member_agent_id="forecast-member",
        arguments={"station": "A"},
        invocation_sequence=0,
        idempotency_key="tool-call-1",
    )

    assert response["value"] == {"ok": True}
    assert captured["payload"]["member_agent_id"] == "forecast-member"


def test_team_artifact_capability_registration_forwards_exact_invocation_tuple():
    captured = {}
    capability = "c" * 43

    def handler(request):
        captured["payload"] = json.loads(request.content)
        return httpx.Response(201, json={"capability": capability})

    client = RunnerGatewayClient.from_execution_request(
        _request(), transport=httpx.MockTransport(handler)
    )

    result = client.register_artifact_capability(
        team_version_id="team-version-1",
        member_agent_id="forecast-member",
        task_id="forecast-task",
        invocation_id="persisted-invocation-7",
    )

    assert result == capability
    assert captured["payload"] == {
        "team_version_id": "team-version-1",
        "member_agent_id": "forecast-member",
        "task_id": "forecast-task",
        "invocation_id": "persisted-invocation-7",
    }


def test_client_rejects_oversized_or_invalid_responses():
    oversized = RunnerGatewayClient.from_execution_request(
        _request(),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, content=b"x" * 101)
        ),
        max_response_bytes=100,
    )
    invalid = RunnerGatewayClient.from_execution_request(
        _request(),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"unexpected": True})
        ),
    )

    with pytest.raises(RunnerGatewayResponseInvalid):
        oversized.get_snapshot()
    with pytest.raises(RunnerGatewayResponseInvalid):
        invalid.get_snapshot()


def test_execution_request_clamps_every_http_timeout_phase_to_execution_deadline():
    captured = {}
    request = _request().model_copy(
        update={
            "deadline_at": datetime.now(timezone.utc) + timedelta(seconds=5),
            "execution_deadline_at": datetime.now(timezone.utc)
            + timedelta(seconds=0.8),
        }
    )

    def handler(http_request):
        captured.update(http_request.extensions["timeout"])
        return httpx.Response(
            200,
            json={
                "snapshot_id": "snapshot-1",
                "run_id": "run-1",
                "digest": "a" * 64,
                "payload": {
                    "snapshot_id": "snapshot-1",
                    "run_id": "run-1",
                    "unit_id": "unit-1",
                    "project_id": "project-1",
                    "user_id": "user-1",
                    "actor": {
                        "id": "agent-1",
                        "name": "Agent",
                        "description": "",
                        "runtime_form": "common",
                        "language": "zh-CN",
                        "system_prompt": "",
                        "context_prompt": "",
                        "approval_policy": "never",
                    },
                    "model": {"provider_id": "provider-1", "model": "model-1"},
                    "messages": [],
                    "limits": {"snapshot_max_bytes": 1048576},
                    "created_at": "2026-08-14T10:00:00Z",
                },
            },
        )

    client = RunnerGatewayClient.from_execution_request(
        request, transport=httpx.MockTransport(handler)
    )

    client.get_snapshot()

    assert set(captured) == {"connect", "pool", "read", "write"}
    assert all(0 < value <= 0.8 for value in captured.values())


def test_expired_execution_call_opens_no_transport_and_completion_uses_control_budget():
    calls = []

    def handler(http_request):
        calls.append(
            (http_request.url.path, dict(http_request.extensions["timeout"]))
        )
        return httpx.Response(200, json={})

    request = _request().model_copy(
        update={
            "deadline_at": datetime.now(timezone.utc) + timedelta(seconds=5),
            "execution_deadline_at": datetime.now(timezone.utc)
            - timedelta(seconds=1),
        }
    )
    client = RunnerGatewayClient.from_execution_request(
        request, transport=httpx.MockTransport(handler)
    )

    with pytest.raises(RunnerGatewayUnavailable):
        client.get_snapshot()
    assert calls == []

    client.complete(
        {"status": "failed", "error_code": "sandbox_timeout"},
        "completion:failed",
    )

    assert len(calls) == 1
    assert calls[0][0].endswith("/completion")
    assert all(0 < value <= 1.0 for value in calls[0][1].values())

    with pytest.raises(RunnerGatewayUnavailable):
        client.get_snapshot()
    assert len(calls) == 1


def test_completion_does_not_widen_concurrent_execution_request_budget():
    completion_entered = Event()
    release_completion = Event()
    calls = []
    errors = []

    def handler(http_request):
        calls.append(
            (http_request.url.path, dict(http_request.extensions["timeout"]))
        )
        if http_request.url.path.endswith("/completion"):
            completion_entered.set()
            assert release_completion.wait(timeout=3)
            return httpx.Response(200, json={})
        return httpx.Response(
            200,
            json={
                "snapshot_id": "snapshot-1",
                "run_id": "run-1",
                "digest": "a" * 64,
                "payload": {
                    "snapshot_id": "snapshot-1",
                    "run_id": "run-1",
                    "unit_id": "unit-1",
                    "project_id": "project-1",
                    "user_id": "user-1",
                    "actor": {
                        "id": "agent-1",
                        "name": "Agent",
                        "description": "",
                        "runtime_form": "common",
                        "language": "zh-CN",
                        "system_prompt": "",
                        "context_prompt": "",
                        "approval_policy": "never",
                    },
                    "model": {"provider_id": "provider-1", "model": "model-1"},
                    "messages": [],
                    "limits": {"snapshot_max_bytes": 1048576},
                    "created_at": "2026-08-14T10:00:00Z",
                },
            },
        )

    request = _request().model_copy(
        update={
            "deadline_at": datetime.now(timezone.utc) + timedelta(seconds=5),
            "execution_deadline_at": datetime.now(timezone.utc)
            + timedelta(seconds=0.75),
        }
    )
    client = RunnerGatewayClient.from_execution_request(
        request, transport=httpx.MockTransport(handler)
    )

    def complete():
        try:
            client.complete(
                {"status": "failed", "error_code": "sandbox_timeout"},
                "completion:failed",
            )
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    completion_thread = Thread(target=complete)
    completion_thread.start()
    assert completion_entered.wait(timeout=2)
    client.get_snapshot()
    release_completion.set()
    completion_thread.join(timeout=2)

    assert not completion_thread.is_alive()
    assert errors == []
    execution_timeout = next(
        timeout for path, timeout in calls if path.endswith("/snapshot")
    )
    completion_timeout = next(
        timeout for path, timeout in calls if path.endswith("/completion")
    )
    assert all(0 < value <= 0.75 for value in execution_timeout.values())
    assert all(0 < value <= 1.0 for value in completion_timeout.values())


def test_completion_budget_is_capped_by_shorter_request_deadline():
    captured = {}

    def handler(http_request):
        captured.update(http_request.extensions["timeout"])
        return httpx.Response(200, json={})

    request_deadline = datetime.now(timezone.utc) + timedelta(seconds=0.5)
    request = _request().model_copy(
        update={
            "deadline_at": request_deadline,
            "execution_deadline_at": request_deadline,
        }
    )
    client = RunnerGatewayClient.from_execution_request(
        request, transport=httpx.MockTransport(handler)
    )

    client.complete(
        {"status": "failed", "error_code": "sandbox_timeout"},
        "completion:failed",
    )

    assert set(captured) == {"connect", "pool", "read", "write"}
    assert all(0 < value <= 0.5 for value in captured.values())


def test_execution_request_enforces_absolute_slow_drip_deadline():
    snapshot_body = json.dumps(
        {
            "snapshot_id": "snapshot-1",
            "run_id": "run-1",
            "digest": "a" * 64,
            "payload": {
                "snapshot_id": "snapshot-1",
                "run_id": "run-1",
                "unit_id": "unit-1",
                "project_id": "project-1",
                "user_id": "user-1",
                "actor": {
                    "id": "agent-1",
                    "name": "Agent",
                    "description": "",
                    "runtime_form": "common",
                    "language": "zh-CN",
                    "system_prompt": "",
                    "context_prompt": "",
                    "approval_policy": "never",
                },
                "model": {"provider_id": "provider-1", "model": "model-1"},
                "messages": [],
                "limits": {"snapshot_max_bytes": 1048576},
                "created_at": "2026-08-14T10:00:00Z",
            },
        }
    ).encode()
    server, thread = _start_slow_drip_server(snapshot_body, gap_seconds=0.05)
    request = _request().model_copy(
        update={
            "deadline_at": datetime.now(timezone.utc) + timedelta(seconds=5),
            "execution_deadline_at": datetime.now(timezone.utc)
            + timedelta(seconds=0.25),
            "gateway_url": f"http://127.0.0.1:{server.server_port}",
        }
    )
    client = RunnerGatewayClient.from_execution_request(request)
    started_at = time.monotonic()
    operation_elapsed = None
    try:
        with pytest.raises(RunnerGatewayDeadlineExceeded) as captured:
            client.get_snapshot()
        operation_elapsed = time.monotonic() - started_at
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert operation_elapsed is not None and operation_elapsed < 0.45
    assert captured.value.code == "sandbox_timeout"


def test_completion_enforces_one_second_absolute_slow_drip_deadline():
    completion_body = b"{" + (b" " * 22) + b"}"
    server, thread = _start_slow_drip_server(
        completion_body,
        gap_seconds=0.12,
    )
    request = _request().model_copy(
        update={
            "deadline_at": datetime.now(timezone.utc) + timedelta(seconds=5),
            "execution_deadline_at": datetime.now(timezone.utc)
            + timedelta(seconds=5),
            "gateway_url": f"http://127.0.0.1:{server.server_port}",
        }
    )
    client = RunnerGatewayClient.from_execution_request(request)
    started_at = time.monotonic()
    operation_elapsed = None
    try:
        with pytest.raises(RunnerGatewayUnavailable) as captured:
            client.complete(
                {"status": "failed", "error_code": "sandbox_timeout"},
                "completion:failed",
            )
        operation_elapsed = time.monotonic() - started_at
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert operation_elapsed is not None and operation_elapsed < 1.25
    assert captured.value.code == "runner_gateway_unavailable"


def test_execution_deadline_is_rechecked_after_response_validation():
    class SlowAdapter:
        @staticmethod
        def validate_python(payload):
            time.sleep(0.2)
            return payload

    request = _request().model_copy(
        update={
            "execution_deadline_at": datetime.now(timezone.utc)
            + timedelta(seconds=0.1)
        }
    )
    client = RunnerGatewayClient.from_execution_request(
        request,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"ok": True})
        ),
    )

    with pytest.raises(RunnerGatewayDeadlineExceeded):
        client._request_adapter("GET", "snapshot", SlowAdapter())


def _start_slow_drip_server(body: bytes, *, gap_seconds: float):
    class SlowDripHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_GET(self):
            self._write_body()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self._write_body()

        def _write_body(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            chunk_size = max(1, len(body) // 12)
            try:
                for offset in range(0, len(body), chunk_size):
                    self.wfile.write(body[offset : offset + chunk_size])
                    self.wfile.flush()
                    time.sleep(gap_seconds)
            except OSError:
                return

        def log_message(self, _format, *_args):
            return

    class TestServer(ThreadingHTTPServer):
        daemon_threads = True

    server = TestServer(("127.0.0.1", 0), SlowDripHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
