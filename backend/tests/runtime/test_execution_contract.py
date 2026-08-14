from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.runtime.execution_contract import RunExecutionRequest, RunExecutionResult
from app.runtime.run_worker import load_execution_request


def test_execution_request_accepts_only_run_snapshot_references_and_future_deadline():
    deadline = datetime.now(timezone.utc) + timedelta(minutes=5)

    request = RunExecutionRequest(
        run_id="run-1",
        agent_version="agent-v1",
        checkpoint_key="checkpoint-1",
        deadline_at=deadline,
        snapshot_id="snapshot-1",
        snapshot_digest="a" * 64,
        gateway_url="http://api:8000/internal/runner",
        run_token="secret-token",
    )

    assert request.model_dump(mode="json") == {
        "run_id": "run-1",
        "agent_version": "agent-v1",
        "checkpoint_key": "checkpoint-1",
        "deadline_at": deadline.isoformat().replace("+00:00", "Z"),
        "snapshot_id": "snapshot-1",
        "snapshot_digest": "a" * 64,
        "gateway_url": "http://api:8000/internal/runner",
        "run_token": "secret-token",
    }
    assert "secret-token" not in repr(request)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", "../escape"),
        ("agent_version", "agent/v1"),
        ("checkpoint_key", ""),
        ("deadline_at", datetime.now(timezone.utc) - timedelta(seconds=1)),
    ],
)
def test_execution_request_rejects_invalid_or_expired_references(field, value):
    data = {
        "run_id": "run-1",
        "agent_version": "agent-v1",
        "checkpoint_key": "checkpoint-1",
        "deadline_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        "snapshot_id": "snapshot-1",
        "snapshot_digest": "a" * 64,
        "gateway_url": "http://api:8000/internal/runner",
        "run_token": "secret-token",
    }
    data[field] = value

    with pytest.raises(ValidationError):
        RunExecutionRequest(**data)


def test_execution_request_rejects_untrusted_runtime_fields():
    with pytest.raises(ValidationError):
        RunExecutionRequest(
            run_id="run-1",
            agent_version="agent-v1",
            checkpoint_key="checkpoint-1",
            deadline_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            snapshot_id="snapshot-1",
            snapshot_digest="a" * 64,
            gateway_url="http://api:8000/internal/runner",
            run_token="secret-token",
            image="ubuntu:latest",
            command=["sh", "-c", "id"],
            workspace_path="C:/host-data",
        )


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "interrupted"])
def test_execution_result_uses_bounded_terminal_statuses(status):
    result = RunExecutionResult(
        status=status,
        error_code="sandbox_failed" if status == "failed" else None,
        artifact_refs=("artifact-1",),
        checkpoint_key="checkpoint-1",
    )

    assert result.status == status
    assert result.artifact_refs == ("artifact-1",)


def test_execution_result_rejects_unknown_status_and_unbounded_error_text():
    with pytest.raises(ValidationError):
        RunExecutionResult(status="running")
    with pytest.raises(ValidationError):
        RunExecutionResult(status="failed", error_code="/var/run/docker.sock: secret")


def test_worker_loads_validated_request_from_environment(monkeypatch):
    deadline = datetime.now(timezone.utc) + timedelta(minutes=5)
    monkeypatch.setenv("IAP_RUN_EXECUTION_REQUEST", RunExecutionRequest(
        run_id="run-1",
        agent_version="agent-v1",
        checkpoint_key="runtime",
        deadline_at=deadline,
        snapshot_id="snapshot-1",
        snapshot_digest="a" * 64,
        gateway_url="http://api:8000/internal/runner",
        run_token="secret-token",
    ).model_dump_json())

    assert load_execution_request().run_id == "run-1"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("snapshot_digest", "A" * 64),
        ("snapshot_digest", "short"),
        ("gateway_url", "file:///tmp/gateway"),
        ("gateway_url", "http://user:password@api/internal/runner"),
        ("run_token", ""),
    ],
)
def test_execution_request_rejects_invalid_gateway_identity(field, value):
    data = {
        "run_id": "run-1",
        "agent_version": "agent-v1",
        "checkpoint_key": "checkpoint-1",
        "deadline_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        "snapshot_id": "snapshot-1",
        "snapshot_digest": "a" * 64,
        "gateway_url": "http://api:8000/internal/runner",
        "run_token": "secret-token",
    }
    data[field] = value

    with pytest.raises(ValidationError):
        RunExecutionRequest(**data)
