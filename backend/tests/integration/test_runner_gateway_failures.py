from datetime import timedelta

import pytest
from sqlalchemy import select

from app.approvals.models import Approval
from app.approvals.service import ApprovalService
from app.core.request_context import RequestContext
from app.runtime.execution_snapshot import RuntimeExecutionSnapshot
from app.runtime.model_gateway import ModelUpstreamError
from app.tools.schemas import ToolExecutionContext


def _tool_request(tool_id="system.get_current_time", version="1.0.0"):
    return {
        "tool_call_id": f"call-{tool_id}",
        "tool_id": tool_id,
        "version": version,
        "arguments": {},
        "invocation_sequence": 0,
    }


def test_expired_revoked_and_cross_run_tokens_fail_safely(runner_gateway_env):
    env = runner_gateway_env
    expired = env.issue_token(lifetime_seconds=1)
    env.clock.value += timedelta(seconds=2)
    expired_response = env.client.get(
        "/internal/runner/runs/run-1/snapshot",
        headers=env.headers(expired),
    )

    env.clock.value -= timedelta(seconds=2)
    revoked = env.issue_token()
    env.token_service.revoke("run-1", "acceptance")
    revoked_response = env.client.get(
        "/internal/runner/runs/run-1/snapshot",
        headers=env.headers(revoked),
    )

    run_two_token = env.issue_token("run-2")
    cross_run_response = env.client.get(
        "/internal/runner/runs/run-1/snapshot",
        headers=env.headers(run_two_token),
    )

    assert expired_response.status_code == 401
    assert expired_response.json()["code"] == "run_token_expired"
    assert revoked_response.status_code == 401
    assert revoked_response.json()["code"] == "run_token_invalid"
    assert cross_run_response.status_code == 404
    combined = expired_response.text + revoked_response.text + cross_run_response.text
    assert "runner-gateway-acceptance-signing-key" not in combined
    assert "I:\\" not in combined


def test_snapshot_mismatch_and_idempotency_conflicts_are_stable(runner_gateway_env):
    env = runner_gateway_env
    token = env.issue_token()
    row = env.session.get(RuntimeExecutionSnapshot, "snapshot-run-1")
    row.digest = "a" * 64
    env.session.commit()

    mismatch = env.client.get(
        "/internal/runner/runs/run-1/snapshot",
        headers=env.headers(token),
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["code"] == "snapshot_invalid"


def test_duplicate_event_and_completion_are_idempotent(runner_gateway_env):
    env = runner_gateway_env
    token = env.issue_token()
    event_request = {
        "sequence": 1,
        "event_type": "runner.started",
        "payload": {"status": "running"},
    }
    first_event = env.client.post(
        "/internal/runner/runs/run-1/events",
        headers=env.headers(token, "event:1"),
        json=event_request,
    )
    replay_event = env.client.post(
        "/internal/runner/runs/run-1/events",
        headers=env.headers(token, "event:1"),
        json=event_request,
    )
    changed_event = env.client.post(
        "/internal/runner/runs/run-1/events",
        headers=env.headers(token, "event:1"),
        json={**event_request, "payload": {"status": "changed"}},
    )

    completion_request = {"status": "failed", "error_code": "sandbox_timeout"}
    first_completion = env.client.post(
        "/internal/runner/runs/run-1/completion",
        headers=env.headers(token, "completion:failed"),
        json=completion_request,
    )
    replay_completion = env.client.post(
        "/internal/runner/runs/run-1/completion",
        headers=env.headers(token, "completion:failed"),
        json=completion_request,
    )

    assert first_event.json() == replay_event.json()
    assert changed_event.status_code == 409
    assert changed_event.json()["code"] == "idempotency_conflict"
    assert first_completion.json() == replay_completion.json()
    assert env.repository.get_run_by_id("run-1").status == "failed"


def test_disabled_and_unauthorized_tools_return_403(runner_gateway_env):
    env = runner_gateway_env
    token = env.issue_token()
    unauthorized = env.client.post(
        "/internal/runner/runs/run-1/tool-invocations",
        headers=env.headers(token, "tool:unauthorized"),
        json=_tool_request("system.not_authorized"),
    )
    env.tool_store.set_enabled("system.get_current_time", False)
    disabled = env.client.post(
        "/internal/runner/runs/run-1/tool-invocations",
        headers=env.headers(token, "tool:disabled"),
        json=_tool_request(),
    )

    assert unauthorized.status_code == 403
    assert unauthorized.json()["code"] == "tool_not_authorized"
    assert disabled.status_code == 403
    assert disabled.json()["code"] == "tool_not_authorized"


def test_action_scoped_token_cannot_invoke_tool(runner_gateway_env):
    env = runner_gateway_env
    token = env.issue_token(actions={"snapshot.read"})

    response = env.client.post(
        "/internal/runner/runs/run-1/tool-invocations",
        headers=env.headers(token, "tool:forbidden"),
        json=_tool_request(),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "runner_action_forbidden"


def test_approval_interruption_can_resume_authorized_execution(runner_gateway_env):
    env = runner_gateway_env
    token = env.issue_token()
    interrupted = env.client.post(
        "/internal/runner/runs/run-1/tool-invocations",
        headers=env.headers(token, "tool:approval"),
        json=_tool_request("system.get_runtime_context"),
    )

    assert interrupted.status_code == 409
    assert interrupted.json()["code"] == "tool_approval_required"
    approval = env.session.scalar(select(Approval).where(Approval.run_id == "run-1"))
    assert approval is not None
    admin_context = RequestContext(
        user_id="admin-1",
        unit_id="unit-1",
        project_id="project-1",
        roles=frozenset({"project_admin"}),
    )
    ApprovalService(env.session, clock=env.clock).approve(
        approval.id,
        admin_context,
        "acceptance approved",
    )
    env.session.commit()
    result = env.tool_gateway.execute_approved(
        approval.id,
        ToolExecutionContext(
            unit_id="unit-1",
            run_id="run-1",
            conversation_id="conversation-run-1",
            project_id="project-1",
            user_id="user-1",
            actor_roles=("user",),
        ),
    )

    assert result.value["run_id"] == "run-1"
    assert env.repository.list_tool_invocations("run-1")[0].status == "completed"

    resumed = env.client.post(
        "/internal/runner/runs/run-1/tool-invocations",
        headers=env.headers(token, "tool:approval:resumed"),
        json=_tool_request("system.get_runtime_context"),
    )

    assert resumed.status_code == 200
    assert resumed.json()["value"]["run_id"] == "run-1"
    assert len(env.repository.list_tool_invocations("run-1")) == 1


def test_model_checkpoint_and_artifact_failures_do_not_leak_details(
    runner_gateway_env,
):
    env = runner_gateway_env
    token = env.issue_token()
    env.model_gateway.error = ModelUpstreamError(
        "Authorization: Bearer provider-secret I:\\internal\\provider"
    )
    model = env.client.post(
        "/internal/runner/runs/run-1/model-invocations",
        headers=env.headers(token, "model:timeout"),
        json={
            "messages": [{"role": "user", "content": "test"}],
            "tools": [],
            "invocation_sequence": 0,
        },
    )
    env.checkpoint_store.max_bytes = 16
    checkpoint = env.client.put(
        "/internal/runner/runs/run-1/checkpoints/large",
        headers=env.headers(token, "checkpoint:large"),
        json={"state": {"payload": "x" * 100}},
    )

    assert model.status_code == 502
    assert model.json()["code"] == "model_request_failed"
    assert checkpoint.status_code == 413
    assert checkpoint.json()["code"] == "checkpoint_too_large"
    combined = model.text + checkpoint.text
    assert "provider-secret" not in combined
    assert "I:\\internal" not in combined


@pytest.mark.parametrize(
    ("status", "error_code", "stored_status"),
    [
        ("cancelled", "sandbox_cancelled", "cancelled"),
        ("failed", "sandbox_timeout", "failed"),
        ("failed", "sandbox_oom", "failed"),
        ("failed", "launcher_unavailable", "failed"),
    ],
)
def test_terminal_failures_persist_safe_final_state(
    runner_gateway_env,
    status,
    error_code,
    stored_status,
):
    env = runner_gateway_env
    token = env.issue_token()
    response = env.client.post(
        "/internal/runner/runs/run-1/completion",
        headers=env.headers(token, f"completion:{error_code}"),
        json={"status": status, "error_code": error_code},
    )

    assert response.status_code == 200
    assert env.repository.get_run_by_id("run-1").status == stored_status
    completion = next(
        event
        for event in env.repository.list_events("run-1", 0)
        if event.event_type == "runner.completion"
    )
    assert completion.payload["error_code"] == error_code
