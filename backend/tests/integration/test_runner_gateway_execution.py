import base64
import hashlib

from sqlalchemy import select

from app.audit.models import AuditEvent
from app.conversations.models import Message, ToolInvocation


def test_runner_gateway_normal_path_persists_complete_trace(runner_gateway_env):
    env = runner_gateway_env
    token = env.issue_token()
    headers = env.headers(token)
    run = env.repository.get_run_by_id("run-1")
    assert run.status == "pending"

    run.status = "running"
    env.repository.append_event("run-1", "run.status", {"status": "running"})
    env.session.commit()

    snapshot = env.client.get(
        "/internal/runner/runs/run-1/snapshot",
        headers=headers,
    )
    event = env.client.post(
        "/internal/runner/runs/run-1/events",
        headers=env.headers(token, "event:1"),
        json={
            "sequence": 1,
            "event_type": "runner.started",
            "payload": {"phase": "execute"},
        },
    )
    model = env.client.post(
        "/internal/runner/runs/run-1/model-invocations",
        headers=env.headers(token, "model:0"),
        json={
            "messages": [{"role": "user", "content": "生成验收文件"}],
            "tools": [],
            "invocation_sequence": 0,
        },
    )
    tool = env.client.post(
        "/internal/runner/runs/run-1/tool-invocations",
        headers=env.headers(token, "tool:time:0"),
        json={
            "tool_call_id": "call-time-1",
            "tool_id": "system.get_current_time",
            "version": "1.0.0",
            "arguments": {"timezone": "Asia/Shanghai"},
            "invocation_sequence": 0,
        },
    )
    checkpoint = env.client.put(
        "/internal/runner/runs/run-1/checkpoints/langgraph",
        headers=env.headers(token, "checkpoint:langgraph"),
        json={"state": {"status": "running", "step": "artifact"}},
    )
    data = b"runner gateway acceptance\n"
    artifact = env.client.post(
        "/internal/runner/runs/run-1/artifacts",
        headers=env.headers(token, "artifact:result"),
        json={
            "path": "/artifacts/result.txt",
            "content_type": "text/plain",
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "data_base64": base64.b64encode(data).decode("ascii"),
        },
    )
    completion = env.client.post(
        "/internal/runner/runs/run-1/completion",
        headers=env.headers(token, "completion:final"),
        json={
            "status": "completed",
            "final_assistant_content": model.json()["content"],
            "checkpoint_key": "langgraph",
            "artifact_refs": [artifact.json()["artifact_id"]],
        },
    )

    assert [
        response.status_code
        for response in (snapshot, event, model, tool, checkpoint, artifact, completion)
    ] == [200, 200, 200, 200, 200, 201, 200]
    assert env.repository.get_run_by_id("run-1").status == "completed"
    assert env.checkpoint_store.load_latest("run-1") == {
        "status": "running",
        "step": "artifact",
    }
    invocation = env.session.scalar(
        select(ToolInvocation).where(ToolInvocation.run_id == "run-1")
    )
    assert invocation is not None and invocation.status == "completed"
    audits = list(
        env.session.scalars(
            select(AuditEvent)
            .where(AuditEvent.run_id == "run-1")
            .order_by(AuditEvent.occurred_at, AuditEvent.id)
        )
    )
    assert "llm.invoke.succeeded" in {audit.action for audit in audits}
    assert "tool.invoke.succeeded" in {audit.action for audit in audits}
    messages = list(
        env.session.scalars(
            select(Message)
            .where(Message.conversation_id == "conversation-run-1")
            .order_by(Message.sequence)
        )
    )
    assert [(message.role, message.content) for message in messages] == [
        ("user", "生成验收文件"),
        ("assistant", "任务已完成"),
    ]
    event_types = [
        item.event_type for item in env.repository.list_events("run-1", 0)
    ]
    assert event_types.index("runner.started") < event_types.index("artifact.ready")
    assert event_types.index("artifact.ready") < event_types.index("runner.completion")

    read = env.client.get(
        f"/internal/runner/runs/run-1/artifacts/{artifact.json()['artifact_id']}",
        headers=headers,
    )
    assert base64.b64decode(read.json()["data_base64"]) == data
    history = env.repository.list_runs(
        unit_id="unit-1",
        project_id="project-1",
        owner_id="user-1",
        page=1,
        page_size=20,
    )
    accepted = next(item for item in history.items if item["id"] == "run-1")
    assert accepted["actor_id"] == "agent-1"
    assert accepted["status"] == "completed"
    assert accepted["created_at"] is not None
    assert accepted["duration_ms"] >= 0
