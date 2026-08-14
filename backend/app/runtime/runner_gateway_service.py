from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from time import perf_counter

from sqlalchemy import func, select

from app.audit.recorder import AuditRecorder, AuditRecordRequest
from app.conversations.models import AgentRun, RunEvent
from app.conversations.repository import ConversationRepository
from app.tools.schemas import ToolDefinition

from .checkpoint_store import CheckpointStore, RunnerRequestStore
from .execution_snapshot import (
    ExecutionSnapshotService,
    SnapshotIntegrityError,
    StoredExecutionSnapshot,
    verify_snapshot_digest,
)
from .model_gateway import ModelGateway, ModelRuntimeError, ModelSelection
from .run_tokens import RunTokenClaims
from .runner_gateway_auth import RunnerGatewayError
from .runner_gateway_schemas import (
    CheckpointResponse,
    CheckpointWriteRequest,
    EventAppendRequest,
    EventAppendResponse,
    ModelInvocationRequest,
    ModelInvocationResponse,
    ModelToolCall,
    SnapshotResponse,
)


def _canonical_digest(value: dict[str, object]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _event_payload_limit() -> int:
    raw = os.getenv("IAP_RUNNER_EVENT_PAYLOAD_MAX_BYTES", "65536")
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(
            "IAP_RUNNER_EVENT_PAYLOAD_MAX_BYTES must be positive"
        ) from error
    if value <= 0:
        raise ValueError("IAP_RUNNER_EVENT_PAYLOAD_MAX_BYTES must be positive")
    return value


class RunnerGatewayService:
    def __init__(
        self,
        snapshot_service: ExecutionSnapshotService,
        checkpoint_store: CheckpointStore | None = None,
        conversation_repository: ConversationRepository | None = None,
        model_gateway: ModelGateway | None = None,
        audit_recorder: AuditRecorder | None = None,
        *,
        event_payload_max_bytes: int | None = None,
    ) -> None:
        self.snapshot_service = snapshot_service
        self.checkpoint_store = checkpoint_store
        self.conversation_repository = conversation_repository
        self.model_gateway = model_gateway
        self.audit_recorder = audit_recorder or AuditRecorder()
        self.event_payload_max_bytes = (
            _event_payload_limit()
            if event_payload_max_bytes is None
            else event_payload_max_bytes
        )
        if self.event_payload_max_bytes <= 0:
            raise ValueError("event payload max bytes must be positive")

    def get_snapshot(
        self, run_id: str, claims: RunTokenClaims
    ) -> SnapshotResponse:
        stored = self._verified_snapshot(run_id, claims)
        return SnapshotResponse(
            snapshot_id=stored.snapshot_id,
            run_id=stored.run_id,
            digest=stored.digest,
            payload=stored.payload,
        )

    def _verified_snapshot(
        self, run_id: str, claims: RunTokenClaims
    ) -> StoredExecutionSnapshot:
        try:
            stored = self.snapshot_service.get(claims.snapshot_id)
        except SnapshotIntegrityError as error:
            raise RunnerGatewayError(
                409, "snapshot_invalid", "执行快照校验失败"
            ) from error
        if stored is None or stored.run_id != run_id:
            raise RunnerGatewayError(404, "run_not_found", "Run 不存在")
        if (
            stored.digest != claims.snapshot_digest
            or not verify_snapshot_digest(stored.payload, stored.digest)
        ):
            raise RunnerGatewayError(409, "snapshot_invalid", "执行快照校验失败")
        return stored

    def get_latest_checkpoint(
        self, run_id: str, claims: RunTokenClaims
    ) -> CheckpointResponse:
        store = self._require_checkpoint_store()
        checkpoint = store.load_latest_record(run_id)
        if checkpoint is None:
            raise RunnerGatewayError(
                404, "checkpoint_not_found", "检查点不存在"
            )
        if checkpoint.snapshot_digest != claims.snapshot_digest:
            raise RunnerGatewayError(
                409,
                "checkpoint_snapshot_mismatch",
                "检查点与执行快照不匹配",
            )
        return CheckpointResponse(
            checkpoint_key=checkpoint.checkpoint_key,
            snapshot_digest=claims.snapshot_digest,
            state=checkpoint.state,
        )

    def save_checkpoint(
        self,
        run_id: str,
        checkpoint_key: str,
        request: CheckpointWriteRequest,
        claims: RunTokenClaims,
        idempotency_key: str,
    ) -> CheckpointResponse:
        store = self._require_checkpoint_store()
        repository = self._require_conversation_repository()
        self._lock_run(repository, run_id)
        requests = RunnerRequestStore(repository.session)
        action = "checkpoint.write"
        request_digest = _canonical_digest(
            {
                "checkpoint_key": checkpoint_key,
                "snapshot_digest": claims.snapshot_digest,
                "state": request.state,
            }
        )
        replay = self._replay_or_conflict(
            requests, run_id, action, idempotency_key, request_digest
        )
        if replay is not None:
            return CheckpointResponse.model_validate(replay)
        try:
            checkpoint = store.save(
                run_id,
                checkpoint_key,
                request.state,
                claims.snapshot_digest,
                idempotency_key,
                commit=False,
            )
        except ValueError as error:
            code = (
                "checkpoint_too_large"
                if "size limit" in str(error)
                else "checkpoint_invalid"
            )
            status_code = 413 if code == "checkpoint_too_large" else 400
            raise RunnerGatewayError(
                status_code,
                code,
                "检查点超过大小限制" if status_code == 413 else "检查点无效",
            ) from error
        response = CheckpointResponse(
            checkpoint_key=checkpoint.checkpoint_key,
            snapshot_digest=claims.snapshot_digest,
            state=checkpoint.state,
        )
        requests.add(
            run_id=run_id,
            action=action,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            response_json=response.model_dump(mode="json"),
        )
        repository.session.commit()
        return response

    def append_event(
        self,
        run_id: str,
        request: EventAppendRequest,
        _claims: RunTokenClaims,
        idempotency_key: str,
    ) -> EventAppendResponse:
        repository = self._require_conversation_repository()
        try:
            encoded_payload = json.dumps(
                request.payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise RunnerGatewayError(
                400, "event_payload_invalid", "事件数据无效"
            ) from error
        if len(encoded_payload) > self.event_payload_max_bytes:
            raise RunnerGatewayError(
                413, "event_payload_too_large", "事件数据超过大小限制"
            )

        self._lock_run(repository, run_id)
        requests = RunnerRequestStore(repository.session)
        action = "event.append"
        request_digest = _canonical_digest(request.model_dump(mode="json"))
        replay = self._replay_or_conflict(
            requests, run_id, action, idempotency_key, request_digest
        )
        if replay is not None:
            return EventAppendResponse.model_validate(replay)

        expected_sequence = requests.count(run_id, action) + 1
        if request.sequence != expected_sequence:
            raise RunnerGatewayError(
                409, "event_sequence_invalid", "Runner 事件序号无效"
            )
        platform_sequence = int(
            repository.session.scalar(
                select(func.coalesce(func.max(RunEvent.sequence), 0)).where(
                    RunEvent.run_id == run_id
                )
            )
            or 0
        ) + 1
        event = RunEvent(
            run_id=run_id,
            sequence=platform_sequence,
            event_type=request.event_type,
            payload=request.payload,
        )
        repository.add(event)
        response = EventAppendResponse(
            sequence=platform_sequence,
            runner_sequence=request.sequence,
            event_type=request.event_type,
        )
        requests.add(
            run_id=run_id,
            action=action,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            response_json=response.model_dump(mode="json"),
        )
        repository.session.commit()
        return response

    def invoke_model(
        self,
        run_id: str,
        request: ModelInvocationRequest,
        claims: RunTokenClaims,
        idempotency_key: str,
    ) -> ModelInvocationResponse:
        repository = self._require_conversation_repository()
        model_gateway = self._require_model_gateway()
        snapshot = self._verified_snapshot(run_id, claims)
        self._lock_run(repository, run_id)
        requests = RunnerRequestStore(repository.session)
        action = "model.invoke"
        request_digest = _canonical_digest(
            {
                "snapshot_digest": claims.snapshot_digest,
                "request": request.model_dump(mode="json"),
            }
        )
        replay = self._replay_or_conflict(
            requests, run_id, action, idempotency_key, request_digest
        )
        if replay is not None:
            return ModelInvocationResponse.model_validate(replay)

        selection = ModelSelection(
            snapshot.payload.model.provider_id,
            snapshot.payload.model.model,
        )
        messages = [
            message.model_dump(mode="json", exclude_none=True)
            for message in request.messages
        ]
        tools = [
            ToolDefinition(tool.tool_id, tool.description, tool.input_schema)
            for tool in request.tools
        ]
        started = perf_counter()
        try:
            result = model_gateway.generate(messages, selection, tools=tools)
        except ModelRuntimeError as error:
            repository.session.rollback()
            try:
                self._record_model_audit(
                    repository,
                    snapshot,
                    request,
                    selection,
                    succeeded=False,
                    duration_ms=max(
                        0,
                        round((perf_counter() - started) * 1000),
                    ),
                )
                repository.session.commit()
            except Exception as audit_error:
                repository.session.rollback()
                raise RunnerGatewayError(
                    500,
                    "audit_persistence_failed",
                    "模型调用记录保存失败",
                ) from audit_error
            raise RunnerGatewayError(
                502, "model_request_failed", "模型调用失败"
            ) from error

        response = ModelInvocationResponse(
            content=result.content,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
            tool_calls=[
                ModelToolCall(
                    id=call.id,
                    name=call.name,
                    arguments=call.arguments,
                )
                for call in result.tool_calls
            ],
        )
        duration_ms = max(0, round((perf_counter() - started) * 1000))
        try:
            self._record_model_audit(
                repository,
                snapshot,
                request,
                selection,
                succeeded=True,
                duration_ms=duration_ms,
                response=response,
            )
            requests.add(
                run_id=run_id,
                action=action,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                response_json=response.model_dump(mode="json"),
            )
            repository.session.commit()
        except Exception as error:
            repository.session.rollback()
            raise RunnerGatewayError(
                500, "audit_persistence_failed", "模型调用记录保存失败"
            ) from error
        return response

    def _record_model_audit(
        self,
        repository: ConversationRepository,
        snapshot: StoredExecutionSnapshot,
        request: ModelInvocationRequest,
        selection: ModelSelection,
        *,
        succeeded: bool,
        duration_ms: int,
        response: ModelInvocationResponse | None = None,
    ) -> None:
        run = repository.get_run_by_id(snapshot.run_id)
        if run is None:
            raise RunnerGatewayError(404, "run_not_found", "Run 不存在")
        metadata = {
            "provider": selection.provider_id,
            "model": selection.model,
            "iteration": request.invocation_sequence,
        }
        if response is not None:
            metadata.update(
                {
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "total_tokens": response.total_tokens,
                }
            )
        status = "succeeded" if succeeded else "failed"
        self.audit_recorder.record(
            repository.session,
            AuditRecordRequest(
                unit_id=snapshot.payload.unit_id,
                project_id=snapshot.payload.project_id,
                user_id=snapshot.payload.user_id,
                actor_roles=tuple(run.actor_roles_json),
                authorization_scope="project",
                event_scope="project",
                category="runtime",
                source="llm",
                action=f"llm.invoke.{status}",
                status=status,
                risk_level="low" if succeeded else "medium",
                trace_id=snapshot.run_id,
                run_id=snapshot.run_id,
                resource_type="model",
                resource_id=selection.model,
                idempotency_key=(
                    f"llm:{snapshot.run_id}:"
                    f"{request.invocation_sequence}:{status}"
                ),
                occurred_at=datetime.now(UTC),
                duration_ms=duration_ms,
                metadata=metadata,
                allowed_metadata_keys=frozenset(metadata),
                error_code=None if succeeded else "model_request_failed",
            ),
        )

    def _require_checkpoint_store(self) -> CheckpointStore:
        if self.checkpoint_store is None:
            raise RuntimeError("checkpoint store is required")
        return self.checkpoint_store

    def _require_conversation_repository(self) -> ConversationRepository:
        if self.conversation_repository is None:
            raise RuntimeError("conversation repository is required")
        return self.conversation_repository

    def _require_model_gateway(self) -> ModelGateway:
        if self.model_gateway is None:
            raise RuntimeError("model gateway is required")
        return self.model_gateway

    @staticmethod
    def _lock_run(repository: ConversationRepository, run_id: str) -> AgentRun:
        run = repository.session.scalar(
            select(AgentRun).where(AgentRun.id == run_id).with_for_update()
        )
        if run is None:
            raise RunnerGatewayError(404, "run_not_found", "Run 不存在")
        return run

    @staticmethod
    def _replay_or_conflict(
        requests: RunnerRequestStore,
        run_id: str,
        action: str,
        idempotency_key: str,
        request_digest: str,
    ) -> dict[str, object] | None:
        stored = requests.get(run_id, action, idempotency_key)
        if stored is None:
            return None
        if stored.request_digest != request_digest:
            raise RunnerGatewayError(
                409,
                "idempotency_conflict",
                "幂等键已用于其他请求",
            )
        return dict(stored.response_json)
