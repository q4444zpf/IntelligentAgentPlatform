from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.artifacts.service import ArtifactService
from app.artifacts.storage import S3ObjectStorage
from app.audit.recorder import AuditRecorder
from app.conversations.repository import ConversationRepository
from app.core.database import get_session
from app.mcp.protocol import McpProtocolClient
from app.mcp.store import McpStore
from app.tools.gateway import ToolGateway
from app.tools.store import ToolStore

from .checkpoint_store import CheckpointStore
from .execution_snapshot import ExecutionSnapshotService
from .model_gateway import ModelGateway, OpenAICompatibleModelGateway
from .run_tokens import RunTokenClaims, RunTokenService
from .runner_gateway_auth import default_token_service, require_runner_action
from .runner_gateway_schemas import (
    ArtifactContentResponse,
    ArtifactCreateRequest,
    ArtifactFileResponse,
    CheckpointResponse,
    CheckpointWriteRequest,
    CompletionRequest,
    CompletionResponse,
    EventAppendRequest,
    EventAppendResponse,
    ModelInvocationRequest,
    ModelInvocationResponse,
    SnapshotResponse,
    ToolInvocationRequest,
    ToolInvocationResponse,
)
from .runner_gateway_service import RunnerGatewayService


def default_snapshot_service(
    session: Annotated[Session, Depends(get_session)],
) -> ExecutionSnapshotService:
    return ExecutionSnapshotService(session, None, None)


def default_checkpoint_store(
    session: Annotated[Session, Depends(get_session)],
) -> CheckpointStore:
    return CheckpointStore(session)


def default_conversation_repository(
    session: Annotated[Session, Depends(get_session)],
) -> ConversationRepository:
    return ConversationRepository(session)


def default_model_gateway() -> ModelGateway:
    return OpenAICompatibleModelGateway()


def default_audit_recorder() -> AuditRecorder:
    return AuditRecorder()


def default_artifact_service(
    session: Annotated[Session, Depends(get_session)],
) -> ArtifactService:
    return ArtifactService(session, S3ObjectStorage())


def default_tool_gateway(
    repository: Annotated[
        ConversationRepository,
        Depends(default_conversation_repository),
    ],
) -> ToolGateway:
    return ToolGateway(
        tool_store=ToolStore(),
        repository=repository,
        mcp_store=McpStore(),
        mcp_protocol_client=McpProtocolClient(),
    )


def create_router(
    *,
    token_service_dependency: Callable[..., RunTokenService] = default_token_service,
    snapshot_service_dependency: Callable[..., ExecutionSnapshotService] = default_snapshot_service,
    checkpoint_store_dependency: Callable[..., CheckpointStore] = default_checkpoint_store,
    conversation_repository_dependency: Callable[..., ConversationRepository] = default_conversation_repository,
    model_gateway_dependency: Callable[..., ModelGateway] = default_model_gateway,
    audit_recorder_dependency: Callable[..., AuditRecorder] = default_audit_recorder,
    tool_gateway_dependency: Callable[..., ToolGateway] = default_tool_gateway,
    artifact_service_dependency: Callable[..., ArtifactService] = default_artifact_service,
    event_payload_max_bytes: int | None = None,
) -> APIRouter:
    router = APIRouter()
    snapshot_claims = require_runner_action(
        "snapshot.read", token_service_dependency
    )
    checkpoint_read_claims = require_runner_action(
        "checkpoint.read", token_service_dependency
    )
    checkpoint_write_claims = require_runner_action(
        "checkpoint.write", token_service_dependency
    )
    event_append_claims = require_runner_action(
        "event.append", token_service_dependency
    )
    model_invoke_claims = require_runner_action(
        "model.invoke", token_service_dependency
    )
    tool_invoke_claims = require_runner_action(
        "tool.invoke", token_service_dependency
    )
    artifact_claims = require_runner_action(
        "artifact.create", token_service_dependency
    )
    completion_claims = require_runner_action(
        "result.complete", token_service_dependency
    )

    @router.get(
        "/runs/{run_id}/snapshot",
        response_model=SnapshotResponse,
    )
    def get_snapshot(
        run_id: str,
        claims: Annotated[RunTokenClaims, Depends(snapshot_claims)],
        snapshot_service: Annotated[
            ExecutionSnapshotService,
            Depends(snapshot_service_dependency),
        ],
    ) -> SnapshotResponse:
        return RunnerGatewayService(snapshot_service).get_snapshot(run_id, claims)

    @router.get(
        "/runs/{run_id}/checkpoints/latest",
        response_model=CheckpointResponse,
    )
    def get_latest_checkpoint(
        run_id: str,
        claims: Annotated[RunTokenClaims, Depends(checkpoint_read_claims)],
        snapshot_service: Annotated[
            ExecutionSnapshotService,
            Depends(snapshot_service_dependency),
        ],
        checkpoint_store: Annotated[
            CheckpointStore,
            Depends(checkpoint_store_dependency),
        ],
        repository: Annotated[
            ConversationRepository,
            Depends(conversation_repository_dependency),
        ],
    ) -> CheckpointResponse:
        return RunnerGatewayService(
            snapshot_service, checkpoint_store, repository
        ).get_latest_checkpoint(run_id, claims)

    @router.put(
        "/runs/{run_id}/checkpoints/{checkpoint_key}",
        response_model=CheckpointResponse,
    )
    def save_checkpoint(
        run_id: str,
        checkpoint_key: str,
        request: CheckpointWriteRequest,
        idempotency_key: Annotated[
            str,
            Header(
                alias="Idempotency-Key",
                min_length=1,
                max_length=200,
            ),
        ],
        claims: Annotated[RunTokenClaims, Depends(checkpoint_write_claims)],
        snapshot_service: Annotated[
            ExecutionSnapshotService,
            Depends(snapshot_service_dependency),
        ],
        checkpoint_store: Annotated[
            CheckpointStore,
            Depends(checkpoint_store_dependency),
        ],
        repository: Annotated[
            ConversationRepository,
            Depends(conversation_repository_dependency),
        ],
    ) -> CheckpointResponse:
        return RunnerGatewayService(
            snapshot_service, checkpoint_store, repository
        ).save_checkpoint(
            run_id, checkpoint_key, request, claims, idempotency_key
        )

    @router.post(
        "/runs/{run_id}/events",
        response_model=EventAppendResponse,
    )
    def append_event(
        run_id: str,
        request: EventAppendRequest,
        idempotency_key: Annotated[
            str,
            Header(
                alias="Idempotency-Key",
                min_length=1,
                max_length=200,
            ),
        ],
        claims: Annotated[RunTokenClaims, Depends(event_append_claims)],
        snapshot_service: Annotated[
            ExecutionSnapshotService,
            Depends(snapshot_service_dependency),
        ],
        checkpoint_store: Annotated[
            CheckpointStore,
            Depends(checkpoint_store_dependency),
        ],
        repository: Annotated[
            ConversationRepository,
            Depends(conversation_repository_dependency),
        ],
    ) -> EventAppendResponse:
        return RunnerGatewayService(
            snapshot_service,
            checkpoint_store,
            repository,
            event_payload_max_bytes=event_payload_max_bytes,
        ).append_event(run_id, request, claims, idempotency_key)

    @router.post(
        "/runs/{run_id}/model-invocations",
        response_model=ModelInvocationResponse,
    )
    def invoke_model(
        run_id: str,
        request: ModelInvocationRequest,
        idempotency_key: Annotated[
            str,
            Header(
                alias="Idempotency-Key",
                min_length=1,
                max_length=200,
            ),
        ],
        claims: Annotated[RunTokenClaims, Depends(model_invoke_claims)],
        snapshot_service: Annotated[
            ExecutionSnapshotService,
            Depends(snapshot_service_dependency),
        ],
        repository: Annotated[
            ConversationRepository,
            Depends(conversation_repository_dependency),
        ],
        model_gateway: Annotated[
            ModelGateway,
            Depends(model_gateway_dependency),
        ],
        audit_recorder: Annotated[
            AuditRecorder,
            Depends(audit_recorder_dependency),
        ],
    ) -> ModelInvocationResponse:
        return RunnerGatewayService(
            snapshot_service,
            conversation_repository=repository,
            model_gateway=model_gateway,
            audit_recorder=audit_recorder,
        ).invoke_model(run_id, request, claims, idempotency_key)

    @router.post(
        "/runs/{run_id}/tool-invocations",
        response_model=ToolInvocationResponse,
    )
    def invoke_tool(
        run_id: str,
        request: ToolInvocationRequest,
        idempotency_key: Annotated[
            str,
            Header(
                alias="Idempotency-Key",
                min_length=1,
                max_length=200,
            ),
        ],
        claims: Annotated[RunTokenClaims, Depends(tool_invoke_claims)],
        snapshot_service: Annotated[
            ExecutionSnapshotService,
            Depends(snapshot_service_dependency),
        ],
        repository: Annotated[
            ConversationRepository,
            Depends(conversation_repository_dependency),
        ],
        tool_gateway: Annotated[
            ToolGateway,
            Depends(tool_gateway_dependency),
        ],
    ) -> ToolInvocationResponse:
        return RunnerGatewayService(
            snapshot_service,
            conversation_repository=repository,
            tool_gateway=tool_gateway,
        ).invoke_tool(run_id, request, claims, idempotency_key)

    @router.post(
        "/runs/{run_id}/artifacts",
        response_model=ArtifactFileResponse,
        status_code=201,
    )
    def create_artifact(
        run_id: str,
        request: ArtifactCreateRequest,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
        claims: Annotated[RunTokenClaims, Depends(artifact_claims)],
        snapshot_service: Annotated[
            ExecutionSnapshotService,
            Depends(snapshot_service_dependency),
        ],
        repository: Annotated[
            ConversationRepository,
            Depends(conversation_repository_dependency),
        ],
        artifacts: Annotated[
            ArtifactService,
            Depends(artifact_service_dependency),
        ],
    ) -> ArtifactFileResponse:
        return RunnerGatewayService(
            snapshot_service,
            conversation_repository=repository,
            artifact_service=artifacts,
        ).create_artifact(run_id, request, claims, idempotency_key)

    @router.get(
        "/runs/{run_id}/artifacts",
        response_model=list[ArtifactFileResponse],
    )
    def list_artifacts(
        run_id: str,
        claims: Annotated[RunTokenClaims, Depends(artifact_claims)],
        snapshot_service: Annotated[
            ExecutionSnapshotService,
            Depends(snapshot_service_dependency),
        ],
        artifacts: Annotated[
            ArtifactService,
            Depends(artifact_service_dependency),
        ],
    ) -> list[ArtifactFileResponse]:
        return RunnerGatewayService(
            snapshot_service,
            artifact_service=artifacts,
        ).list_artifacts(run_id, claims)

    @router.get(
        "/runs/{run_id}/artifacts/{artifact_id}",
        response_model=ArtifactContentResponse,
    )
    def read_artifact(
        run_id: str,
        artifact_id: str,
        claims: Annotated[RunTokenClaims, Depends(artifact_claims)],
        snapshot_service: Annotated[
            ExecutionSnapshotService,
            Depends(snapshot_service_dependency),
        ],
        artifacts: Annotated[
            ArtifactService,
            Depends(artifact_service_dependency),
        ],
    ) -> ArtifactContentResponse:
        return RunnerGatewayService(
            snapshot_service,
            artifact_service=artifacts,
        ).read_artifact(run_id, artifact_id, claims)

    @router.post(
        "/runs/{run_id}/completion",
        response_model=CompletionResponse,
    )
    def complete(
        run_id: str,
        request: CompletionRequest,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=200),
        ],
        claims: Annotated[RunTokenClaims, Depends(completion_claims)],
        snapshot_service: Annotated[
            ExecutionSnapshotService,
            Depends(snapshot_service_dependency),
        ],
        repository: Annotated[
            ConversationRepository,
            Depends(conversation_repository_dependency),
        ],
        artifacts: Annotated[
            ArtifactService,
            Depends(artifact_service_dependency),
        ],
    ) -> CompletionResponse:
        return RunnerGatewayService(
            snapshot_service,
            conversation_repository=repository,
            artifact_service=artifacts,
        ).complete(run_id, request, claims, idempotency_key)

    return router


router = create_router()
