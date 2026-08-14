from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.conversations.repository import ConversationRepository
from app.core.database import get_session

from .checkpoint_store import CheckpointStore
from .execution_snapshot import ExecutionSnapshotService
from .run_tokens import RunTokenClaims, RunTokenService
from .runner_gateway_auth import default_token_service, require_runner_action
from .runner_gateway_schemas import (
    CheckpointResponse,
    CheckpointWriteRequest,
    EventAppendRequest,
    EventAppendResponse,
    SnapshotResponse,
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


def create_router(
    *,
    token_service_dependency: Callable[..., RunTokenService] = default_token_service,
    snapshot_service_dependency: Callable[..., ExecutionSnapshotService] = default_snapshot_service,
    checkpoint_store_dependency: Callable[..., CheckpointStore] = default_checkpoint_store,
    conversation_repository_dependency: Callable[..., ConversationRepository] = default_conversation_repository,
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

    return router


router = create_router()
