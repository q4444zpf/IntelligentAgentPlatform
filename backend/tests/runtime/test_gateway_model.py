from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.audit.models import AuditEvent
from app.audit.recorder import AuditRecorder
from app.conversations.models import AgentRun, Conversation, Message
from app.conversations.repository import ConversationRepository
from app.db.base import Base
from app.runtime.checkpoint_store import CheckpointStore
from app.runtime.execution_snapshot import (
    ExecutionSnapshotPayload,
    PublishedAgentSnapshot,
    SnapshotModelSelection,
    SnapshotRuntimeLimits,
    StoredExecutionSnapshot,
    canonical_snapshot_bytes,
)
from app.runtime.gateway_model import GatewayChatModel, RunnerGatewayModelError
from app.runtime.model_gateway import (
    ModelResult,
    ModelSelection,
    ModelUpstreamError,
)
from app.runtime.run_tokens import RunTokenClaims
from app.runtime.runner_gateway_auth import (
    RunnerGatewayError,
    runner_gateway_error_handler,
)
from app.runtime.runner_gateway_router import create_router


def build_snapshot():
    payload = ExecutionSnapshotPayload(
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
            approval_policy="never",
        ),
        model=SnapshotModelSelection(
            provider_id="approved-provider",
            model="approved-model",
        ),
        messages=(),
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


class FakeModelGateway:
    def __init__(self, result=None, error=None):
        self.result = result or ModelResult("完成", 5, 3, 8)
        self.error = error
        self.messages = []
        self.selections = []
        self.tools = []

    def generate(self, messages, selection=None, tools=None):
        self.messages.append(messages)
        self.selections.append(selection)
        self.tools.append(tools)
        if self.error is not None:
            raise self.error
        return self.result


def build_model_client(model_gateway, audit_recorder_dependency=AuditRecorder):
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
        title="Gateway model test",
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
        actor_roles_json=["user"],
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
            model_gateway_dependency=lambda: model_gateway,
            audit_recorder_dependency=audit_recorder_dependency,
        ),
        prefix="/internal/runner",
    )
    return TestClient(app), session


def model_request():
    return {
        "provider_id": "attacker-provider",
        "model": "attacker-model",
        "messages": [{"role": "user", "content": "查询水位"}],
        "tools": [
            {
                "tool_id": "water.query_level",
                "description": "查询水位",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
        "temperature": 9,
        "max_output_tokens": 999999,
        "invocation_sequence": 0,
    }


def headers(key="model-1"):
    return {
        "Authorization": "Bearer valid",
        "Idempotency-Key": key,
    }


def test_model_endpoint_uses_snapshot_selection_not_request_override():
    model = FakeModelGateway()
    client, session = build_model_client(model)

    response = client.post(
        "/internal/runner/runs/run-1/model-invocations",
        headers=headers(),
        json=model_request(),
    )

    assert response.status_code == 200
    assert model.selections == [
        ModelSelection("approved-provider", "approved-model")
    ]
    assert response.json() == {
        "content": "完成",
        "prompt_tokens": 5,
        "completion_tokens": 3,
        "total_tokens": 8,
        "tool_calls": [],
    }
    audit = session.scalar(
        select(AuditEvent).where(AuditEvent.action == "llm.invoke.succeeded")
    )
    assert audit is not None
    assert audit.resource_id == "approved-model"
    assert audit.metadata_json["provider"] == "approved-provider"


def test_duplicate_model_request_returns_stored_response_without_second_call():
    model = FakeModelGateway()
    client, _session = build_model_client(model)

    first = client.post(
        "/internal/runner/runs/run-1/model-invocations",
        headers=headers(),
        json=model_request(),
    )
    second = client.post(
        "/internal/runner/runs/run-1/model-invocations",
        headers=headers(),
        json=model_request(),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    assert len(model.selections) == 1


def test_model_failure_returns_safe_code_and_records_failed_audit():
    model = FakeModelGateway(error=ModelUpstreamError("provider-secret"))
    client, session = build_model_client(model)

    response = client.post(
        "/internal/runner/runs/run-1/model-invocations",
        headers=headers(),
        json=model_request(),
    )

    assert response.status_code == 502
    assert response.json()["code"] == "model_request_failed"
    assert "provider-secret" not in response.text
    audit = session.scalar(
        select(AuditEvent).where(AuditEvent.action == "llm.invoke.failed")
    )
    assert audit is not None
    assert audit.error_code == "model_request_failed"


def test_model_failure_maps_audit_storage_error_safely():
    class BrokenAuditRecorder:
        def record(self, _session, _request):
            raise RuntimeError("database-secret")

    model = FakeModelGateway(error=ModelUpstreamError("provider-secret"))
    client, _session = build_model_client(model, BrokenAuditRecorder)

    response = client.post(
        "/internal/runner/runs/run-1/model-invocations",
        headers=headers(),
        json=model_request(),
    )

    assert response.status_code == 500
    assert response.json()["code"] == "audit_persistence_failed"
    assert "database-secret" not in response.text


class FakeModelTransport:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.requests = []

    def invoke_model(self, request, idempotency_key):
        self.requests.append((request, idempotency_key))
        if self.error is not None:
            raise self.error
        return self.response


def test_gateway_chat_model_normalizes_tool_calls():
    transport = FakeModelTransport(
        response={
            "content": None,
            "prompt_tokens": 7,
            "completion_tokens": 4,
            "total_tokens": 11,
            "tool_calls": [
                {
                    "id": "call-1",
                    "name": "water.query_level",
                    "arguments": {"station": "A"},
                }
            ],
        }
    )
    model = GatewayChatModel(transport)
    tool_schema = {
        "type": "function",
        "function": {
            "name": "water.query_level",
            "description": "查询水位",
            "parameters": {"type": "object", "properties": {}},
        },
    }

    message = model.invoke(
        [HumanMessage(content="查询水位")],
        tools=[tool_schema],
    )

    assert isinstance(message, AIMessage)
    assert message.tool_calls == [
        {
            "name": "water.query_level",
            "args": {"station": "A"},
            "id": "call-1",
            "type": "tool_call",
        }
    ]
    request, idempotency_key = transport.requests[0]
    assert request["messages"] == [{"role": "user", "content": "查询水位"}]
    assert request["tools"][0]["tool_id"] == "water.query_level"
    assert idempotency_key == "model-0"


def test_gateway_chat_model_supports_langchain_bind_tools():
    transport = FakeModelTransport(
        response={
            "content": "完成",
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "tool_calls": [],
        }
    )
    model = GatewayChatModel(transport)
    bound = model.bind_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "water.query_level",
                    "description": "查询水位",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
    )

    message = bound.invoke([HumanMessage(content="查询水位")])

    assert message.content == "完成"
    request, _key = transport.requests[0]
    assert request["tools"] == [
        {
            "tool_id": "water.query_level",
            "description": "查询水位",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]


def test_gateway_chat_model_maps_transport_errors_without_leaking_details():
    transport = FakeModelTransport(
        error=RunnerGatewayModelError(
            "model_request_failed",
            "Authorization: Bearer runner-secret",
        )
    )
    model = GatewayChatModel(transport)

    with pytest.raises(RunnerGatewayModelError) as captured:
        model.invoke([HumanMessage(content="test")])

    assert captured.value.code == "model_request_failed"
    assert "runner-secret" not in str(captured.value)
