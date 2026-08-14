import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.service import ArtifactService
from app.conversations.models import AgentRun, Conversation, Message
from app.conversations.repository import ConversationRepository
from app.db.base import Base
from app.runtime.checkpoint_store import CheckpointStore
from app.runtime.execution_snapshot import (
    ExecutionSnapshotPayload,
    ExecutionSnapshotService,
    PublishedAgentSnapshot,
    RuntimeExecutionSnapshot,
    SnapshotMessage,
    SnapshotModelSelection,
    SnapshotRuntimeLimits,
    SnapshotTool,
    StoredExecutionSnapshot,
    canonical_snapshot_bytes,
)
from app.runtime.model_gateway import ModelResult
from app.runtime.run_tokens import RunTokenService
from app.runtime.runner_gateway_auth import (
    RunnerGatewayError,
    runner_gateway_error_handler,
)
from app.runtime.runner_gateway_router import create_router
from app.tools.builtins import BUILTIN_TOOL_DEFINITIONS
from app.tools.gateway import ToolGateway
from app.tools.store import ToolStore

ALL_ACTIONS = {
    "snapshot.read",
    "model.invoke",
    "tool.invoke",
    "checkpoint.read",
    "checkpoint.write",
    "event.append",
    "artifact.create",
    "result.complete",
}


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 14, 10, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


class MemoryObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.fail_put = False

    def put_bytes(self, object_key: str, data: bytes, content_type: str) -> None:
        if self.fail_put:
            raise RuntimeError("storage-password=minio-secret")
        self.objects[object_key] = (data, content_type)

    def get_bytes(self, object_key: str) -> bytes:
        return self.objects[object_key][0]

    def delete_object(self, object_key: str) -> None:
        self.objects.pop(object_key, None)


class DeterministicModelGateway:
    def __init__(self) -> None:
        self.result = ModelResult("任务已完成", 7, 3, 10)
        self.error: Exception | None = None

    def generate(self, _messages, _selection=None, tools=None):
        del tools
        if self.error is not None:
            raise self.error
        return self.result


def _stored_snapshot(run_id: str) -> StoredExecutionSnapshot:
    created_at = datetime(2026, 8, 14, 10, 0, tzinfo=UTC)
    payload = ExecutionSnapshotPayload(
        schema_version="3",
        snapshot_id=f"snapshot-{run_id}",
        run_id=run_id,
        unit_id="unit-1",
        project_id="project-1",
        user_id="user-1",
        actor=PublishedAgentSnapshot(
            id="agent-1",
            name="验收智能体",
            description="Runner Gateway acceptance agent",
            runtime_form="common",
            language="zh-CN",
            system_prompt="完成验收任务。",
            context_prompt="仅使用授权能力。",
            approval_policy="control_commands",
        ),
        model=SnapshotModelSelection(
            provider_id="test-provider",
            model="test-model",
        ),
        messages=(
            SnapshotMessage(
                id=f"message-{run_id}",
                sequence=1,
                role="user",
                content="生成验收文件",
                created_at=created_at,
            ),
        ),
        tools=tuple(
            SnapshotTool(
                tool_id=definition["tool_id"],
                version=definition["version"],
                name=definition["name"],
                description=definition["description"],
                input_schema=definition["input_schema"],
                published=True,
                enabled=True,
                source_available=True,
            )
            for definition in BUILTIN_TOOL_DEFINITIONS
        ),
        limits=SnapshotRuntimeLimits(snapshot_max_bytes=1048576),
        created_at=created_at,
    )
    digest = hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest()
    return StoredExecutionSnapshot(
        snapshot_id=payload.snapshot_id,
        run_id=run_id,
        digest=digest,
        payload=payload,
        created_at=created_at,
        expires_at=None,
    )


@dataclass
class RunnerGatewayEnvironment:
    client: TestClient
    session: Session
    repository: ConversationRepository
    checkpoint_store: CheckpointStore
    artifacts: ArtifactService
    storage: MemoryObjectStorage
    token_service: RunTokenService
    snapshots: dict[str, StoredExecutionSnapshot]
    model_gateway: DeterministicModelGateway
    tool_store: ToolStore
    tool_gateway: ToolGateway
    clock: MutableClock

    def issue_token(
        self,
        run_id: str = "run-1",
        *,
        actions: set[str] | None = None,
        lifetime_seconds: int = 300,
    ) -> str:
        return self.token_service.issue(
            self.snapshots[run_id],
            ALL_ACTIONS if actions is None else actions,
            self.clock() + timedelta(seconds=lifetime_seconds),
        ).value

    @staticmethod
    def headers(token: str, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {token}"}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        return headers


@pytest.fixture
def runner_gateway_env():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    snapshots = {run_id: _stored_snapshot(run_id) for run_id in ("run-1", "run-2")}

    for run_id, snapshot in snapshots.items():
        conversation = Conversation(
            id=f"conversation-{run_id}",
            unit_id="unit-1",
            project_id="project-1",
            owner_id="user-1",
            title=f"验收 {run_id}",
        )
        message = Message(
            id=f"message-{run_id}",
            conversation_id=conversation.id,
            sequence=1,
            role="user",
            content="生成验收文件",
        )
        run = AgentRun(
            id=run_id,
            conversation_id=conversation.id,
            trigger_message_id=message.id,
            actor_type="agent",
            actor_id="agent-1",
            actor_roles_json=["user"],
            status="pending",
        )
        session.add_all(
            [
                conversation,
                message,
                run,
                RuntimeExecutionSnapshot(
                    snapshot_id=snapshot.snapshot_id,
                    run_id=run_id,
                    digest=snapshot.digest,
                    payload=snapshot.payload.model_dump(mode="json"),
                    created_at=snapshot.created_at,
                    expires_at=None,
                ),
            ]
        )
    session.commit()

    tool_store = ToolStore(factory)
    for definition in BUILTIN_TOOL_DEFINITIONS:
        candidate = dict(definition)
        if candidate["tool_id"] == "system.get_runtime_context":
            candidate["requires_approval"] = True
            candidate["risk_level"] = "high"
        tool_store.upsert_builtin(candidate)

    repository = ConversationRepository(session)
    checkpoint_store = CheckpointStore(session)
    storage = MemoryObjectStorage()
    artifacts = ArtifactService(session, storage)
    clock = MutableClock()
    token_service = RunTokenService(
        session,
        signing_key=b"runner-gateway-acceptance-signing-key-2026",
        grace_seconds=0,
        clock=clock,
    )
    model_gateway = DeterministicModelGateway()
    snapshot_service = ExecutionSnapshotService(session, None, None)
    tool_gateway = ToolGateway(
        tool_store=tool_store,
        repository=repository,
        clock=clock,
    )

    app = FastAPI()
    app.add_exception_handler(RunnerGatewayError, runner_gateway_error_handler)
    app.include_router(
        create_router(
            token_service_dependency=lambda: token_service,
            snapshot_service_dependency=lambda: snapshot_service,
            checkpoint_store_dependency=lambda: checkpoint_store,
            conversation_repository_dependency=lambda: repository,
            model_gateway_dependency=lambda: model_gateway,
            tool_gateway_dependency=lambda: tool_gateway,
            artifact_service_dependency=lambda: artifacts,
        ),
        prefix="/internal/runner",
    )
    environment = RunnerGatewayEnvironment(
        client=TestClient(app),
        session=session,
        repository=repository,
        checkpoint_store=checkpoint_store,
        artifacts=artifacts,
        storage=storage,
        token_service=token_service,
        snapshots=snapshots,
        model_gateway=model_gateway,
        tool_store=tool_store,
        tool_gateway=tool_gateway,
        clock=clock,
    )
    yield environment
    environment.client.close()
    session.close()
    engine.dispose()
