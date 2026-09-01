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
from .execution_snapshot import PublishedTeamSnapshot
from .team_graph import TeamLimitError, TeamPlan, TeamTask, member_agent_snapshot, validate_team_plan
from .gateway_model import GatewayChatModel, RunnerGatewayModelError
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
            limits = snapshot.payload.limits
            model = GatewayChatModel(
                self.gateway,
                max_iterations=limits.max_iterations,
                max_tool_calls=limits.max_tool_calls,
                max_subagents=limits.max_subagents,
                max_output_bytes=limits.max_output_bytes,
            )
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
            messages = [
                {"role": message.role, "content": message.content}
                for message in snapshot.payload.messages
            ]
            metadata = {
                "snapshot_id": snapshot.snapshot_id,
                "snapshot_digest": snapshot.digest,
                "project_id": snapshot.payload.project_id,
            }
            if isinstance(actor, PublishedTeamSnapshot):
                if len(actor.members) > limits.max_subagents:
                    raise TeamLimitError("team_limit_exceeded: max_subagents")
                plan = TeamPlan(tasks=tuple(
                    TeamTask(
                        id=f"member-{position + 1}", member_id=member.agent_id,
                        objective=member.responsibility, position=position,
                    )
                    for position, member in enumerate(actor.members)
                ))
                validate_team_plan(plan, actor)
                team_fields = {"team_id": actor.id, "version_id": actor.version_id}
                self._append_event("team.plan.created", {
                    **team_fields, "member_ids": [member.agent_id for member in actor.members],
                    "task_ids": [task.id for task in plan.tasks],
                })
                member_results: list[str] = []
                failed_members: list[str] = []
                for task in plan.tasks:
                    self._append_event("team.task.started", {
                        **team_fields, "agent_id": task.member_id,
                        "task_id": task.id, "position": task.position,
                    })
                    try:
                        graph = self.agent_factory.build(
                            member_agent_snapshot(actor, task.member_id),
                            model=model, tools=tools,
                            backend=ArtifactBackend(self.gateway, provenance={
                                "team_version_id": actor.version_id,
                                "member_agent_id": task.member_id,
                                "task_id": task.id,
                            }),
                        )
                        member_result = self.runtime_adapter_type(graph).invoke(
                            RuntimeState(run_id=request.run_id, messages=messages, status="running"),
                            metadata=metadata,
                        )
                    except RunnerApprovalInterruption:
                        raise
                    except Exception:
                        failed_members.append(task.member_id)
                        self._append_event("team.task.failed", {
                            **team_fields, "agent_id": task.member_id,
                            "task_id": task.id, "position": task.position,
                        })
                        if actor.failure_strategy == "fail_fast":
                            raise
                    else:
                        member_results.append(f"{task.member_id}: {member_result.content}")
                        self._append_event("team.task.completed", {
                            **team_fields, "agent_id": task.member_id,
                            "task_id": task.id, "position": task.position,
                        })
                self._append_event("team.synthesis.started", {
                    **team_fields, "completed_members": len(member_results),
                    "failed_members": len(failed_members),
                })
                synthesis_messages = [*messages, {
                    "role": "user",
                    "content": "Synthesize these bounded member results:\n" + "\n".join(member_results),
                }]
                graph = self.agent_factory.build(
                    member_agent_snapshot(actor, actor.supervisor.agent_id),
                    model=model, tools=tools,
                    backend=ArtifactBackend(self.gateway, provenance={
                        "team_version_id": actor.version_id,
                        "member_agent_id": actor.supervisor.agent_id,
                        "task_id": "synthesis",
                    }),
                )
                result = self.runtime_adapter_type(graph, checkpoint_store=checkpoint_store).invoke(
                    RuntimeState(run_id=request.run_id, messages=synthesis_messages, status="running"),
                    metadata=metadata,
                )
                self._append_event("team.synthesis.completed", {
                    **team_fields, "partial": bool(failed_members),
                })
            else:
                graph = self.agent_factory.build(
                    FactoryAgentSnapshot(
                        agent_id=actor.id, name=actor.name,
                        system_prompt=actor.system_prompt, context_prompt=context_prompt,
                        tools=(),
                    ),
                    model=model, tools=tools, backend=backend,
                )
                result = self.runtime_adapter_type(graph, checkpoint_store=checkpoint_store).invoke(
                    RuntimeState(run_id=request.run_id, messages=messages, status="running"),
                    metadata=metadata,
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
        except RunnerGatewayModelError as error:
            return self._fail(error.code)
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
