import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.control.executors import ControlExecutorRegistry


CONFIG = {"reservoir": {"base_url": "https://control.example/api", "token_env": "IAP_CONTROL_EXECUTOR_RESERVOIR_TOKEN", "timeout_seconds": 10}}


def registry(value=CONFIG, handler=None, *, environment="production", secrets=None):
    transport = httpx.MockTransport(handler) if handler else None
    return ControlExecutorRegistry.from_json(
        json.dumps(value), lambda name: (secrets or {"IAP_CONTROL_EXECUTOR_RESERVOIR_TOKEN": "test-token"}).get(name),
        environment, http_transport=transport,
    )


def command():
    return SimpleNamespace(
        id="command-1", project_id="project-1", policy_digest="a" * 64,
        parameter_digest="b" * 64, target={"asset_type": "reservoir", "id": "dam-1"},
        parameters={"opening": 25}, simulation_digest="c" * 64,
        valid_until=datetime(2026, 10, 2, 12, tzinfo=timezone.utc),
    )


def test_production_rejects_http_executor_url_and_missing_secret():
    with pytest.raises(ValueError, match="HTTPS"):
        registry({"reservoir": {**CONFIG["reservoir"], "base_url": "http://control.example/api"}})
    with pytest.raises(ValueError, match="secret"):
        registry(secrets={"IAP_CONTROL_EXECUTOR_RESERVOIR_TOKEN": ""})
    with pytest.raises(ValueError, match="duplicate"):
        ControlExecutorRegistry.from_json(
            '{"reservoir":{},"reservoir":{}}', lambda _name: "test-token", "production"
        )
    with pytest.raises(ValueError, match="asset type"):
        registry({" ": CONFIG["reservoir"]})


def test_http_executor_sends_bearer_digest_and_stable_idempotency_key():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"outcome": "succeeded", "external_id": "physical-1", "summary": {"state": "open"}})

    executor = registry(handler=handler).executors["reservoir"]
    result = executor.execute(command(), "control:command-1:execute")
    assert result.outcome == "succeeded"
    assert result.external_id == "physical-1"
    request = requests[0]
    assert (request.method, str(request.url)) == ("POST", "https://control.example/api/commands/execute")
    assert request.headers["authorization"] == "Bearer test-token"
    assert request.headers["idempotency-key"] == "control:command-1:execute"
    assert json.loads(request.content) == {
        "command_id": "command-1", "project_id": "project-1", "policy_digest": "a" * 64,
        "parameter_digest": "b" * 64, "target": {"asset_type": "reservoir", "id": "dam-1"},
        "parameters": {"opening": 25}, "simulation_digest": "c" * 64,
        "valid_until": "2026-10-02T12:00:00+00:00",
    }
    executor.rollback(command(), {"opening": 0}, "control:command-1:rollback")
    rollback_request = requests[1]
    assert (rollback_request.method, str(rollback_request.url)) == ("POST", "https://control.example/api/commands/rollback")
    assert rollback_request.headers["idempotency-key"] == "control:command-1:rollback"
    assert json.loads(rollback_request.content)["rollback_plan"] == {"opening": 0}


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"location": "https://other.example/commands"}),
    httpx.Response(200, content=b"x" * 65537),
    httpx.Response(200, json={"outcome": "approved"}),
    httpx.Response(200, json={"outcome": "succeeded", "unexpected": "field"}),
])
def test_http_executor_rejects_redirect_oversized_or_invalid_result(response):
    executor = registry(handler=lambda _request: response).executors["reservoir"]
    with pytest.raises(ValueError, match="executor response"):
        executor.execute(command(), "control:command-1:execute")
