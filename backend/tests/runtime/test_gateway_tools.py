import json
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import ToolMessage
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.conversations.models import AgentRun, Conversation, Message
from app.conversations.repository import ConversationRepository
from app.db.base import Base
from app.runtime.checkpoint_store import CheckpointStore
from app.runtime.execution_snapshot import (
    ExecutionSnapshotPayload,
    PublishedAgentSnapshot,
    SnapshotModelSelection,
    SnapshotRuntimeLimits,
    SnapshotTool,
    StoredExecutionSnapshot,
    canonical_snapshot_bytes,
)
from app.runtime.gateway_tools import (
    RunnerApprovalInterruption,
    RunnerGatewayToolError,
    build_gateway_tools,
)
from app.runtime.run_tokens import RunTokenClaims
from app.runtime.runner_gateway_auth import (
    RunnerGatewayError,
    runner_gateway_error_handler,
)
from app.runtime.runner_gateway_router import create_router
from app.tools.schemas import ToolExecutionResult, ToolRuntimeError


def build_snapshot():
    payload = ExecutionSnapshotPayload(
        schema_version="2",
        snapshot_id="snapshot-1",
        run_id="run-1",
        unit_id="unit-1",
        project_id="project-1",
        user_id="user-1",
        actor=PublishedAgentSnapshot(
            id="agent-1",
            name="Agent",
            description="",
            runtime_form="common",
            language="zh-CN",
            system_prompt="",
            context_prompt="",
            approval_policy="control_commands",
        ),
        model=SnapshotModelSelection(provider_id="provider-1", model="model-1"),
        messages=(),
        tools=(
            SnapshotTool(
                tool_id="water.query_level",
                version="3",
                name="查询水位",
                description="查询测站水位",
                input_schema={
                    "type": "object",
                    "properties": {"station": {"type": "string"}},
                    "required": ["station"],
                },
                published=True,
                enabled=True,
                source_available=True,
            ),
            SnapshotTool(
                tool_id="reservoir.release",
                version="1",
                name="水库泄洪",
                description="执行泄洪控制",
                input_schema={"type": "object", "properties": {}},
                published=True,
                enabled=True,
                source_available=True,
            ),
        ),
        limits=SnapshotRuntimeLimits(snapshot_max_bytes=1048576),
        created_at=datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
    )
    import hashlib

    return StoredExecutionSnapshot(
        snapshot_id=payload.snapshot_id,
        run_id=payload.run_id,
        digest=hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest(),
        payload=payload,
        created_at=payload.created_at,
        expires_at=None,
    )


class FakeTokenService:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def verify(self, _token, run_id, action):
        return RunTokenClaims(
            iss="iap-api",
            aud="iap-runner-gateway",
            jti="token-1",
            run_id=run_id,
            unit_id="unit-1",
            project_id="project-1",
            snapshot_id=self.snapshot.snapshot_id,
            snapshot_digest=self.snapshot.digest,
            actions=(action,),
            iat=1,
            nbf=1,
            exp=9999999999,
        )


class FakeSnapshotService:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def get(self, snapshot_id):
        return self.snapshot if snapshot_id == self.snapshot.snapshot_id else None


class FakeToolStore:
    def __init__(self):
        self.enabled = {
            "water.query_level": True,
            "reservoir.release": True,
        }
        self.versions = {
            "water.query_level": "3",
            "reservoir.release": "1",
        }

    def get_executable(self, tool_id):
        if not self.enabled.get(tool_id):
            return None
        return {"tool_id": tool_id, "version": self.versions[tool_id]}


class FakeToolGateway:
    def __init__(self):
        self.tool_store = FakeToolStore()
        self.calls = []

    def execute(self, call, context, authorized_tool_ids):
        self.calls.append((call, context, authorized_tool_ids))
        if call.name == "reservoir.release":
            error = ToolRuntimeError(
                "approval_required",
                "该工具需要人工审批后才能执行。",
            )
            error.approval_id = "approval-1"
            raise error
        if call.name not in authorized_tool_ids:
            raise ToolRuntimeError("tool_not_authorized", "该工具当前不可用。")
        if self.tool_store.get_executable(call.name) is None:
            raise ToolRuntimeError("tool_not_authorized", "该工具当前不可用。")
        return ToolExecutionResult("invocation-1", {"level": 12.3})


def build_client(tool_gateway):
    snapshot = build_snapshot()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    conversation = Conversation(
        id="conversation-1",
        unit_id="unit-1",
        project_id="project-1",
        owner_id="user-1",
        title="Gateway tool test",
    )
    message = Message(
        id="message-1",
        conversation_id=conversation.id,
        sequence=1,
        role="user",
        content="test",
    )
    run = AgentRun(
        id="run-1",
        conversation_id=conversation.id,
        trigger_message_id=message.id,
        actor_type="agent",
        actor_id="agent-1",
        actor_roles_json=["operator"],
        status="running",
    )
    session.add_all([conversation, message, run])
    session.commit()
    repository = ConversationRepository(session)
    checkpoint_store = CheckpointStore(session)
    app = FastAPI()
    app.add_exception_handler(RunnerGatewayError, runner_gateway_error_handler)
    app.include_router(
        create_router(
            token_service_dependency=lambda: FakeTokenService(snapshot),
            snapshot_service_dependency=lambda: FakeSnapshotService(snapshot),
            checkpoint_store_dependency=lambda: checkpoint_store,
            conversation_repository_dependency=lambda: repository,
            tool_gateway_dependency=lambda: tool_gateway,
        ),
        prefix="/internal/runner",
    )
    return TestClient(app), repository


def headers(key="tool-1"):
    return {
        "Authorization": "Bearer valid",
        "Idempotency-Key": key,
    }


def invoke_tool(client, tool_id, *, version="3", key="tool-1"):
    return client.post(
        "/internal/runner/runs/run-1/tool-invocations",
        headers=headers(key),
        json={
            "tool_call_id": f"call-{tool_id}",
            "tool_id": tool_id,
            "version": version,
            "arguments": {"station": "A"},
            "invocation_sequence": 0,
        },
    )


def test_tool_must_be_in_snapshot_and_currently_enabled():
    gateway = FakeToolGateway()
    client, _repository = build_client(gateway)

    missing = invoke_tool(client, "not.snapshotted")
    gateway.tool_store.enabled["water.query_level"] = False
    disabled = invoke_tool(
        client,
        "water.query_level",
        key="tool-2",
    )

    assert missing.status_code == 403
    assert missing.json()["code"] == "tool_not_authorized"
    assert disabled.status_code == 403
    assert disabled.json()["code"] == "tool_not_authorized"
    assert gateway.calls == []


def test_tool_version_must_match_snapshot():
    gateway = FakeToolGateway()
    client, _repository = build_client(gateway)

    response = invoke_tool(client, "water.query_level", version="2")

    assert response.status_code == 403
    assert response.json()["code"] == "tool_not_authorized"
    assert gateway.calls == []


def test_tool_execution_context_comes_from_run_repository_and_is_idempotent():
    gateway = FakeToolGateway()
    client, _repository = build_client(gateway)

    first = invoke_tool(client, "water.query_level")
    second = invoke_tool(client, "water.query_level")

    assert first.status_code == 200
    assert second.json() == first.json()
    assert len(gateway.calls) == 1
    _call, context, authorized = gateway.calls[0]
    assert context.unit_id == "unit-1"
    assert context.project_id == "project-1"
    assert context.user_id == "user-1"
    assert context.actor_roles == ("operator",)
    assert authorized == {"water.query_level", "reservoir.release"}


def test_approval_required_is_returned_as_interruption():
    client, _repository = build_client(FakeToolGateway())

    response = invoke_tool(client, "reservoir.release", version="1")

    assert response.status_code == 409
    assert response.json()["code"] == "tool_approval_required"
    assert response.json()["approval_id"] == "approval-1"


class FakeRunnerGatewayClient:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def invoke_tool(self, **request):
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return {"level": 12.3}


def test_build_gateway_tools_uses_snapshot_schema_and_model_tool_call_id():
    client = FakeRunnerGatewayClient()
    tool = build_gateway_tools(build_snapshot().payload, client)[0]

    result = tool.run(
        {"station": "A"},
        tool_call_id="model-call-1",
    )

    assert isinstance(result, ToolMessage)
    assert json.loads(result.content) == {"level": 12.3}
    assert client.calls == [
        {
            "tool_id": "water.query_level",
            "version": "3",
            "tool_call_id": "model-call-1",
            "arguments": {"station": "A"},
            "invocation_sequence": 0,
            "idempotency_key": "tool:model-call-1:0",
        }
    ]
    assert tool.args_schema == build_snapshot().payload.tools[0].input_schema


def test_gateway_tool_maps_approval_to_typed_interruption():
    client = FakeRunnerGatewayClient(
        RunnerGatewayToolError(
            "tool_approval_required",
            approval_id="approval-1",
        )
    )
    tool = build_gateway_tools(build_snapshot().payload, client)[1]

    try:
        tool.run({}, tool_call_id="model-call-2")
    except RunnerApprovalInterruption as error:
        assert error.approval_id == "approval-1"
        assert "approval-1" not in str(error)
    else:
        raise AssertionError("expected approval interruption")
