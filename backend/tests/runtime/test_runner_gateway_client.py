from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.runtime.execution_contract import RunExecutionRequest
from app.runtime.runner_gateway_client import (
    RunnerGatewayBusinessError,
    RunnerGatewayClient,
    RunnerGatewayResponseInvalid,
    RunnerGatewayUnavailable,
)


def _request():
    return RunExecutionRequest(
        run_id="run-1",
        agent_version="agent-v1",
        checkpoint_key="checkpoint-1",
        deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
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
