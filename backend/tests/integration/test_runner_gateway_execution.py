import base64
import hashlib

from sqlalchemy import select

from app.audit.models import AuditEvent
from app.conversations.models import Message, ToolInvocation
from app.runtime.execution_snapshot import (
    PublishedAgentSnapshot,
    PublishedTeamSnapshot,
    RuntimeExecutionSnapshot,
    SnapshotModelSelection,
    SnapshotTeamMember,
    canonical_snapshot_bytes,
)
from app.runtime.model_gateway import ModelResult


def _install_team_snapshot(env):
    stored = env.snapshots["run-1"]
    tools = {tool.tool_id: tool for tool in stored.payload.tools}
    supervisor_tool = tools["system.get_runtime_context"]
    member_tool = tools["system.get_current_time"]
    supervisor = SnapshotTeamMember(
        agent_id="supervisor",
        role="supervisor",
        responsibility="coordinate",
        agent_definition_digest="a" * 64,
        agent=PublishedAgentSnapshot(
            id="supervisor",
            name="Supervisor",
            description="",
            runtime_form="common",
            language="zh-CN",
            system_prompt="supervise",
            context_prompt="",
            approval_policy="never",
        ),
        model=SnapshotModelSelection(
            provider_id="supervisor-provider", model="supervisor-model"
        ),
        tool_ids=(supervisor_tool.tool_id,),
        tools=(supervisor_tool,),
    )
    member = SnapshotTeamMember(
        agent_id="member-1",
        role="member",
        responsibility="inspect",
        agent_definition_digest="b" * 64,
        agent=PublishedAgentSnapshot(
            id="member-1",
            name="Member",
            description="",
            runtime_form="common",
            language="zh-CN",
            system_prompt="inspect",
            context_prompt="",
            approval_policy="never",
        ),
        model=SnapshotModelSelection(
            provider_id="member-provider", model="member-model"
        ),
        tool_ids=(member_tool.tool_id,),
        tools=(member_tool,),
    )
    actor = PublishedTeamSnapshot(
        id="team-1",
        version_id="team-version-1",
        version=1,
        definition_digest="c" * 64,
        supervisor=supervisor,
        members=(member,),
        max_steps=2,
        max_parallel_members=1,
        timeout_seconds=60,
        failure_strategy="fail_fast",
        tool_ids=(supervisor_tool.tool_id, member_tool.tool_id),
        name="Team",
        description="",
        runtime_form="common",
        language="zh-CN",
        system_prompt="supervise",
        context_prompt="",
        approval_policy="never",
    )
    payload = stored.payload.model_copy(
        update={
            "schema_version": "5",
            "actor": actor,
            "model": supervisor.model,
            "tools": (supervisor_tool, member_tool),
        }
    )
    digest = hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest()
    team_snapshot = stored.model_copy(update={"payload": payload, "digest": digest})
    env.snapshots["run-1"] = team_snapshot
    row = env.session.get(RuntimeExecutionSnapshot, stored.snapshot_id)
    row.digest = digest
    row.payload = payload.model_dump(mode="json")
    run = env.repository.get_run_by_id("run-1")
    run.actor_type = "team"
    run.actor_id = actor.id
    run.actor_version_id = actor.version_id
    env.session.commit()
    return team_snapshot, supervisor_tool, member_tool


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


def test_team_model_invocation_uses_only_the_captured_member_boundary(
    runner_gateway_env, monkeypatch
):
    env = runner_gateway_env
    snapshot, supervisor_tool, member_tool = _install_team_snapshot(env)
    token = env.issue_token()
    calls = []

    def generate(messages, selection, tools=None):
        calls.append((messages, selection, tools))
        return ModelResult("member result", 1, 1, 2)

    monkeypatch.setattr(env.model_gateway, "generate", generate)

    base_request = {
        "provider_id": "member-provider",
        "model": "member-model",
        "messages": [{"role": "user", "content": "inspect"}],
        "tools": [],
        "invocation_sequence": 0,
    }
    missing_member = env.client.post(
        "/internal/runner/runs/run-1/model-invocations",
        headers=env.headers(token, "model:missing-member"),
        json=base_request,
    )
    wrong_model = env.client.post(
        "/internal/runner/runs/run-1/model-invocations",
        headers=env.headers(token, "model:wrong-model"),
        json=base_request
        | {"member_agent_id": "member-1", "model": "supervisor-model"},
    )
    sibling_tool = env.client.post(
        "/internal/runner/runs/run-1/model-invocations",
        headers=env.headers(token, "model:sibling-tool"),
        json=base_request
        | {
            "member_agent_id": "member-1",
            "tools": [
                {
                    "tool_id": supervisor_tool.tool_id,
                    "description": "forged",
                    "input_schema": {"type": "object"},
                }
            ],
        },
    )
    accepted = env.client.post(
        "/internal/runner/runs/run-1/model-invocations",
        headers=env.headers(token, "model:member-1"),
        json=base_request
        | {
            "member_agent_id": "member-1",
            "tools": [
                {
                    "tool_id": member_tool.tool_id,
                    "description": "forged",
                    "input_schema": {"type": "object"},
                }
            ],
        },
    )

    assert [missing_member.status_code, wrong_model.status_code] == [403, 403]
    assert sibling_tool.status_code == 403
    assert accepted.status_code == 200
    assert len(calls) == 1
    _, selection, advertised_tools = calls[0]
    assert (selection.provider_id, selection.model) == (
        "member-provider",
        "member-model",
    )
    assert [(tool.tool_id, tool.description, tool.input_schema) for tool in advertised_tools] == [
        (member_tool.tool_id, member_tool.description, member_tool.input_schema)
    ]
    assert snapshot.payload.schema_version == "5"


def test_team_tool_invocation_is_restricted_to_the_captured_member(
    runner_gateway_env,
):
    env = runner_gateway_env
    _, supervisor_tool, member_tool = _install_team_snapshot(env)
    token = env.issue_token()
    base_request = {
        "tool_call_id": "call-member-time",
        "tool_id": member_tool.tool_id,
        "version": member_tool.version,
        "arguments": {"timezone": "Asia/Shanghai"},
        "invocation_sequence": 0,
    }

    missing_member = env.client.post(
        "/internal/runner/runs/run-1/tool-invocations",
        headers=env.headers(token, "tool:missing-member"),
        json=base_request,
    )
    sibling_tool = env.client.post(
        "/internal/runner/runs/run-1/tool-invocations",
        headers=env.headers(token, "tool:sibling-tool"),
        json=base_request
        | {
            "tool_call_id": "call-supervisor-time",
            "member_agent_id": "supervisor",
        },
    )
    accepted = env.client.post(
        "/internal/runner/runs/run-1/tool-invocations",
        headers=env.headers(token, "tool:member-1"),
        json=base_request | {"member_agent_id": "member-1"},
    )

    assert missing_member.status_code == 403
    assert sibling_tool.status_code == 403
    assert accepted.status_code == 200
    assert supervisor_tool.tool_id != member_tool.tool_id
