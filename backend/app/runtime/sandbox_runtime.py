from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .artifact_backend import ArtifactBackend
from .deepagents_factory import (
    DeepAgentFactory,
)
from .deepagents_factory import (
    PublishedAgentSnapshot as FactoryAgentSnapshot,
)
from .execution_contract import RunExecutionRequest, RunExecutionResult
from .execution_snapshot import verify_snapshot_digest
from .gateway_model import GatewayChatModel
from .gateway_tools import RunnerApprovalInterruption, build_gateway_tools
from .langgraph_runtime import LangGraphRuntimeAdapter, RuntimeState
from .runner_gateway_client import (
    RunnerGatewayBusinessError,
    RunnerGatewayClientError,
)

logger = logging.getLogger(__name__)


@dataclass
class _GatewayCheckpointStore:
    gateway: Any
    snapshot_digest: str

    def load_latest(self, _run_id: str) -> dict[str, Any] | None:
        try:
            checkpoint = self.gateway.get_latest_checkpoint()
        except RunnerGatewayBusinessError as error:
            if error.code in {"checkpoint_not_found", "runner_gateway_not_found"}:
                return None
            raise
        if checkpoint.get("snapshot_digest") != self.snapshot_digest:
            raise ValueError("checkpoint snapshot digest mismatch")
        state = checkpoint.get("state")
        return state if isinstance(state, dict) else None

    def save(self, _run_id: str, checkpoint_key: str, state: dict[str, Any]):
        return self.gateway.save_checkpoint(
            checkpoint_key,
            state,
            f"checkpoint:{checkpoint_key}",
        )


class SandboxRuntime:
    def __init__(
        self,
        gateway,
        *,
        agent_factory: DeepAgentFactory | None = None,
        runtime_adapter_type=LangGraphRuntimeAdapter,
    ) -> None:
        self.gateway = gateway
        self.agent_factory = agent_factory or DeepAgentFactory()
        self.runtime_adapter_type = runtime_adapter_type
        self._event_sequence = 0

    def execute(self, request: RunExecutionRequest) -> RunExecutionResult:
        if request.deadline_at <= datetime.now(timezone.utc):
            return RunExecutionResult(status="failed", error_code="sandbox_timeout")
        try:
            snapshot = self.gateway.get_snapshot()
            if (
                snapshot.snapshot_id != request.snapshot_id
                or snapshot.run_id != request.run_id
                or snapshot.digest != request.snapshot_digest
                or not verify_snapshot_digest(snapshot.payload, snapshot.digest)
            ):
                return RunExecutionResult(status="failed", error_code="snapshot_invalid")

            self._append_event("runner.started", {})
            checkpoint_store = _GatewayCheckpointStore(
                self.gateway, request.snapshot_digest
            )
            model = GatewayChatModel(self.gateway)
            tools = build_gateway_tools(snapshot.payload, self.gateway)
            backend = ArtifactBackend(self.gateway)
            actor = snapshot.payload.actor
            skill_context = ", ".join(skill.name for skill in snapshot.payload.skills)
            context_prompt = actor.context_prompt
            if skill_context:
                context_prompt = (
                    f"{context_prompt}\n\nSkills: {skill_context}"
                    if context_prompt
                    else f"Skills: {skill_context}"
                )
            graph = self.agent_factory.build(
                FactoryAgentSnapshot(
                    agent_id=actor.id,
                    name=actor.name,
                    system_prompt=actor.system_prompt,
                    context_prompt=context_prompt,
                    tools=(),
                ),
                model=model,
                tools=tools,
                backend=backend,
            )
            adapter = self.runtime_adapter_type(graph, checkpoint_store=checkpoint_store)
            result = adapter.invoke(
                RuntimeState(
                    run_id=request.run_id,
                    messages=[
                        {"role": message.role, "content": message.content}
                        for message in snapshot.payload.messages
                    ],
                    status="running",
                ),
                metadata={
                    "snapshot_id": snapshot.snapshot_id,
                    "snapshot_digest": snapshot.digest,
                    "project_id": snapshot.payload.project_id,
                },
            )
            self._append_event("runner.completed", {"status": result.status})
            completion = {
                "status": "completed",
                "final_assistant_content": result.content,
                "checkpoint_key": "langgraph",
                "artifact_refs": [
                    item.artifact_id for item in backend.list("/artifacts")
                ],
            }
            self.gateway.complete(completion, "completion:final")
            return RunExecutionResult(
                status="completed",
                artifact_refs=tuple(completion["artifact_refs"]),
                checkpoint_key="langgraph",
            )
        except RunnerApprovalInterruption as interruption:
            checkpoint_key = f"approval-{interruption.approval_id}"
            state = {"status": "waiting_approval", "approval_id": interruption.approval_id}
            self.gateway.save_checkpoint(
                checkpoint_key,
                state,
                f"checkpoint:{checkpoint_key}",
            )
            self._append_event(
                "approval.required",
                {"approval_id": interruption.approval_id},
            )
            self.gateway.complete(
                {
                    "status": "interrupted",
                    "error_code": "approval_required",
                    "approval_id": interruption.approval_id,
                    "checkpoint_key": checkpoint_key,
                },
                "completion:approval",
            )
            return RunExecutionResult(
                status="interrupted",
                error_code="approval_required",
                checkpoint_key=checkpoint_key,
            )
        except RunnerGatewayClientError as error:
            return self._fail(error.code)
        except Exception:  # noqa: BLE001
            return self._fail("sandbox_failed")

    def _append_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self._event_sequence += 1
        self.gateway.append_event(
            sequence=self._event_sequence,
            event_type=event_type,
            payload=payload,
            idempotency_key=f"event:{self._event_sequence}",
        )

    def _fail(self, error_code: str) -> RunExecutionResult:
        try:
            self.gateway.complete(
                {"status": "failed", "error_code": error_code},
                "completion:failed",
            )
        except Exception:  # noqa: BLE001
            logger.warning("runner completion report failed")
        return RunExecutionResult(status="failed", error_code=error_code)
