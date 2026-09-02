from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from pydantic import ValidationError

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
from .team_graph import (
    TeamBudgetState,
    TeamActiveInvocation,
    TeamLimitError,
    TeamPlan,
    TeamPlanError,
    TeamSchedulerState,
    TeamTask,
    TeamTaskFailure,
    TeamTaskResult,
    member_agent_snapshot,
    parse_supervisor_plan,
    schedule_ready_tasks,
    validate_team_plan,
)
from .gateway_model import GatewayChatModel, GatewayModelBudget, RunnerGatewayModelError
from .gateway_tools import RunnerApprovalInterruption, build_gateway_tools
from .langgraph_runtime import LangGraphRuntimeAdapter, RuntimeResult, RuntimeState
from .runner_gateway_client import (
    RunnerGatewayBusinessError,
    RunnerGatewayClientError,
)

logger = logging.getLogger(__name__)


class _TeamCancelled(RuntimeError):
    pass


class _TeamRecoveryRequired(RuntimeError):
    pass


@dataclass
class _TeamMemberCheckpointStore:
    state: dict[str, Any] | None
    save_callback: Any

    def load_latest(self, _run_id: str) -> dict[str, Any] | None:
        return self.state

    def save(
        self, _run_id: str, checkpoint_key: str, state: dict[str, Any]
    ) -> None:
        self.state = state
        self.save_callback(checkpoint_key, state)


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
        if not isinstance(checkpoint, dict):
            return None
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

            self._event_sequence = 0
            self._approval_checkpoint_key = None
            checkpoint_store = _GatewayCheckpointStore(
                self.gateway, request.snapshot_digest
            )
            limits = snapshot.payload.limits
            backend = ArtifactBackend(self.gateway)
            actor = snapshot.payload.actor
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
                result = self._execute_team(
                    request=request,
                    snapshot=snapshot,
                    actor=actor,
                    messages=messages,
                    metadata=metadata,
                    checkpoint_store=checkpoint_store,
                )
            else:
                self._append_event("runner.started", {})
                model_budget = GatewayModelBudget(
                    max_iterations=limits.max_iterations,
                    max_tool_calls=limits.max_tool_calls,
                    max_subagents=limits.max_subagents,
                )
                skill_context = ", ".join(
                    skill.name for skill in snapshot.payload.skills
                )
                context_prompt = actor.context_prompt
                if skill_context:
                    context_prompt = (
                        f"{context_prompt}\n\nSkills: {skill_context}"
                        if context_prompt
                        else f"Skills: {skill_context}"
                    )
                model = GatewayChatModel(
                    self.gateway,
                    max_iterations=limits.max_iterations,
                    max_tool_calls=limits.max_tool_calls,
                    max_subagents=limits.max_subagents,
                    max_output_bytes=limits.max_output_bytes,
                    budget_state=model_budget,
                )
                tools = build_gateway_tools(snapshot.payload, self.gateway)
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
            if not isinstance(actor, PublishedTeamSnapshot):
                self._append_event("runner.completed", {"status": result.status})
            final_checkpoint_key = (
                "team-scheduler"
                if isinstance(actor, PublishedTeamSnapshot)
                else "langgraph"
            )
            completion = {
                "status": "completed",
                "final_assistant_content": result.content,
                "checkpoint_key": final_checkpoint_key,
                "artifact_refs": [
                    item.artifact_id for item in backend.list("/artifacts")
                ],
            }
            self.gateway.complete(completion, "completion:final")
            return RunExecutionResult(
                status="completed",
                artifact_refs=tuple(completion["artifact_refs"]),
                checkpoint_key=final_checkpoint_key,
            )
        except RunnerApprovalInterruption as interruption:
            checkpoint_key = self._approval_checkpoint_key
            if checkpoint_key is None:
                checkpoint_key = f"approval-{interruption.approval_id}"
                state = {
                    "status": "waiting_approval",
                    "approval_id": interruption.approval_id,
                }
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
        except _TeamCancelled:
            return RunExecutionResult(
                status="cancelled",
                error_code="sandbox_cancelled",
                checkpoint_key="team-scheduler",
            )
        except _TeamRecoveryRequired:
            return self._fail("team_recovery_required")
        except RunnerGatewayModelError as error:
            return self._fail(error.code)
        except RunnerGatewayClientError as error:
            return self._fail(error.code)
        except Exception:  # noqa: BLE001
            return self._fail("sandbox_failed")

    def _execute_team(
        self,
        *,
        request: RunExecutionRequest,
        snapshot,
        actor: PublishedTeamSnapshot,
        messages: list[dict[str, Any]],
        metadata: dict[str, Any],
        checkpoint_store: _GatewayCheckpointStore,
    ) -> RuntimeResult:
        limits = snapshot.payload.limits
        state = self._load_team_state(
            request.run_id,
            actor,
            checkpoint_store,
            limits.max_subagents,
        )
        if state is None:
            self._append_event("runner.started", {})
            model_budget = GatewayModelBudget(
                max_iterations=limits.max_iterations,
                max_tool_calls=limits.max_tool_calls,
                max_subagents=limits.max_subagents,
            )
            plan = self._create_team_plan(
                request,
                snapshot,
                actor,
                messages,
                metadata,
                model_budget,
            )
            team_fields = {"team_id": actor.id, "version_id": actor.version_id}
            self._append_event(
                "team.plan.created",
                {
                    **team_fields,
                    "member_ids": [member.agent_id for member in actor.members],
                    "task_ids": [task.id for task in plan.tasks],
                },
            )
            state = TeamSchedulerState(
                stage="executing",
                snapshot_digest=snapshot.digest,
                team_version_id=actor.version_id,
                plan=plan,
                pending_task_ids=tuple(task.id for task in plan.tasks),
                event_sequence=self._event_sequence,
                budget=self._budget_snapshot(model_budget),
            )
            state = self._save_team_state(state, model_budget)
        else:
            self._event_sequence = state.event_sequence
            model_budget = GatewayModelBudget(
                max_iterations=limits.max_iterations,
                max_tool_calls=limits.max_tool_calls,
                max_subagents=limits.max_subagents,
                next_invocation_sequence=(
                    state.budget.next_invocation_sequence
                ),
                tool_call_count=state.budget.tool_call_count,
                subagent_call_count=state.budget.subagent_call_count,
            )

        legacy_model = None
        legacy_tools = None
        if snapshot.payload.schema_version != "5":
            legacy_model = GatewayChatModel(
                self.gateway,
                max_iterations=limits.max_iterations,
                max_tool_calls=limits.max_tool_calls,
                max_subagents=limits.max_subagents,
                max_output_bytes=limits.max_output_bytes,
                budget_state=model_budget,
            )
            legacy_tools = build_gateway_tools(snapshot.payload, self.gateway)

        completed = {item.task_id: item for item in state.completed_results}
        failed = {item.task_id: item for item in state.failed_results}
        started = set(state.started_task_ids)
        task_by_id = {task.id: task for task in state.plan.tasks}
        team_fields = {"team_id": actor.id, "version_id": actor.version_id}
        state = self._normalize_active_invocations(state)
        recovered = (
            self._recover_completed_invocations(
                state.active_invocations, task_by_id
            )
            if state.stage not in {"synthesizing", "completed"}
            else ()
        )
        if recovered:
            for result in recovered:
                completed[result.task_id] = result
                self._append_event(
                    "team.task.completed",
                    {
                        **team_fields,
                        "agent_id": result.member_agent_id,
                        "task_id": result.task_id,
                        "position": result.position,
                    },
                )
            state = self._state_with_results(
                state,
                completed,
                failed,
                started,
                stage="executing",
                active_invocations=tuple(
                    item
                    for item in state.active_invocations
                    if item.checkpoint_status != "completed"
                ),
            )
            state = self._save_team_state(state, model_budget)

        if state.stage not in {"synthesizing", "completed"}:
            while len(completed) + len(failed) < len(state.plan.tasks):
                blocked = self._blocked_team_tasks(state.plan, completed, failed)
                for task in blocked:
                    failure = TeamTaskFailure(
                        task_id=task.id,
                        member_agent_id=task.member_id,
                        position=task.position,
                        error_code="dependency_failed",
                    )
                    failed[task.id] = failure
                    self._append_event(
                        "team.task.failed",
                        {
                            **team_fields,
                            "agent_id": task.member_id,
                            "task_id": task.id,
                            "position": task.position,
                            "error_code": failure.error_code,
                        },
                    )
                if blocked:
                    state = self._state_with_results(
                        state, completed, failed, started, stage="executing"
                    )
                    state = self._save_team_state(state, model_budget)
                    if actor.failure_strategy == "fail_fast":
                        raise RuntimeError("team member failed")
                    continue

                if state.active_invocations:
                    batch = tuple(
                        task_by_id[item.task_id]
                        for item in state.active_invocations
                        if item.task_id not in completed and item.task_id not in failed
                    )
                else:
                    batch = schedule_ready_tasks(
                        state.plan,
                        set(completed),
                        failed=set(failed),
                        max_parallel_members=min(
                            actor.max_parallel_members,
                            limits.max_subagents,
                        ),
                    )
                if not batch:
                    raise TeamPlanError("team_plan_invalid: task queue stalled")

                self._ensure_team_active(snapshot, actor)
                for task in batch:
                    if task.id not in started:
                        started.add(task.id)
                        self._append_event(
                            "team.task.started",
                            {
                                **team_fields,
                                "agent_id": task.member_id,
                                "task_id": task.id,
                                "position": task.position,
                            },
                        )
                previous_invocations = {
                    item.task_id: item for item in state.active_invocations
                }
                active_invocations = tuple(
                    previous_invocations.get(
                        task.id,
                        TeamActiveInvocation(
                            task_id=task.id,
                            member_agent_id=task.member_id,
                            invocation_id=self._task_invocation_id(actor, task.id),
                            checkpoint_status="running",
                        ),
                    ).model_copy(
                        update={
                            "checkpoint_status": "running",
                            "approval_id": None,
                        }
                    )
                    for task in batch
                )
                state = self._state_with_results(
                    state,
                    completed,
                    failed,
                    started,
                    stage="executing",
                    active_invocations=active_invocations,
                )
                state = self._save_team_state(state, model_budget)
                artifact_capabilities = {
                    invocation.task_id: self.gateway.register_artifact_capability(
                        team_version_id=actor.version_id,
                        member_agent_id=invocation.member_agent_id,
                        task_id=invocation.task_id,
                        invocation_id=invocation.invocation_id,
                    )
                    for invocation in active_invocations
                }
                state_lock = Lock()

                def save_member_runtime_state(task, checkpoint_key, runtime_state):
                    nonlocal state
                    checkpoint_status = (
                        "interrupted"
                        if checkpoint_key == "interrupted"
                        else "completed"
                    )
                    with state_lock:
                        active = tuple(
                            item.model_copy(
                                update={
                                    "runtime_state": runtime_state,
                                    "checkpoint_status": checkpoint_status,
                                }
                            )
                            if item.task_id == task.id
                            else item
                            for item in state.active_invocations
                        )
                        state = self._state_with_results(
                            state,
                            completed,
                            failed,
                            started,
                            stage="executing",
                            active_invocations=active,
                        )
                        state = self._save_team_state(state, model_budget)

                def invoke(task: TeamTask):
                    invocation = next(
                        item
                        for item in active_invocations
                        if item.task_id == task.id
                    )
                    return self._invoke_team_member(
                        request=request,
                        snapshot=snapshot,
                        actor=actor,
                        task=task,
                        messages=messages,
                        metadata=metadata,
                        model_budget=model_budget,
                        completed=completed,
                        legacy_model=legacy_model,
                        legacy_tools=legacy_tools,
                        runtime_state=invocation.runtime_state,
                        invocation=invocation,
                        artifact_capability=artifact_capabilities[task.id],
                        checkpoint_callback=lambda key, runtime_state: (
                            save_member_runtime_state(task, key, runtime_state)
                        ),
                    )

                with ThreadPoolExecutor(max_workers=len(batch)) as executor:
                    futures = [(task, executor.submit(invoke, task)) for task in batch]
                    outcomes = []
                    for task, future in futures:
                        try:
                            outcomes.append((task, future.result(), None))
                        except Exception as error:  # noqa: BLE001
                            outcomes.append((task, None, error))

                interruptions = []
                first_failure = None
                active_by_task = {
                    item.task_id: item for item in state.active_invocations
                }
                for task, member_result, error in sorted(
                    outcomes, key=lambda item: (item[0].position, item[0].id)
                ):
                    if isinstance(error, RunnerApprovalInterruption):
                        interruptions.append((task, error))
                        active_by_task[task.id] = active_by_task[task.id].model_copy(
                            update={
                                "checkpoint_status": "interrupted",
                                "approval_id": error.approval_id,
                            }
                        )
                        continue
                    if error is not None:
                        first_failure = first_failure or error
                        failure = TeamTaskFailure(
                            task_id=task.id,
                            member_agent_id=task.member_id,
                            position=task.position,
                            error_code=self._member_error_code(error),
                        )
                        failed[task.id] = failure
                        self._append_event(
                            "team.task.failed",
                            {
                                **team_fields,
                                "agent_id": task.member_id,
                                "task_id": task.id,
                                "position": task.position,
                                "error_code": failure.error_code,
                            },
                        )
                        active_by_task.pop(task.id, None)
                    else:
                        if (
                            len(member_result.content.encode("utf-8"))
                            > task.output_contract.max_output_bytes
                        ):
                            error = RunnerGatewayModelError("runtime_output_limit")
                            first_failure = first_failure or error
                            failure = TeamTaskFailure(
                                task_id=task.id,
                                member_agent_id=task.member_id,
                                position=task.position,
                                error_code=error.code,
                            )
                            failed[task.id] = failure
                            self._append_event(
                                "team.task.failed",
                                {
                                    **team_fields,
                                    "agent_id": task.member_id,
                                    "task_id": task.id,
                                    "position": task.position,
                                    "error_code": failure.error_code,
                                },
                            )
                            active_by_task.pop(task.id, None)
                            continue
                        completed[task.id] = TeamTaskResult(
                            task_id=task.id,
                            member_agent_id=task.member_id,
                            position=task.position,
                            content=member_result.content,
                        )
                        self._append_event(
                            "team.task.completed",
                            {
                                **team_fields,
                                "agent_id": task.member_id,
                                "task_id": task.id,
                                "position": task.position,
                            },
                        )
                        active_by_task.pop(task.id, None)

                if interruptions:
                    state = self._state_with_results(
                        state,
                        completed,
                        failed,
                        started,
                        stage="waiting_approval",
                        active_invocations=tuple(
                            active_by_task[task.id]
                            for task, _approval in interruptions
                        ),
                    )
                    for task, approval in interruptions:
                        self._append_event(
                            "approval.required",
                            {
                                **team_fields,
                                "approval_id": approval.approval_id,
                                "agent_id": task.member_id,
                                "task_id": task.id,
                                "invocation_id": active_by_task[task.id].invocation_id,
                            },
                        )
                    state = state.model_copy(
                        update={"event_sequence": self._event_sequence}
                    )
                    self._save_team_state(state, model_budget)
                    self._approval_checkpoint_key = "team-scheduler"
                    raise interruptions[0][1]

                state = self._state_with_results(
                    state, completed, failed, started, stage="executing"
                )
                state = self._save_team_state(state, model_budget)
                if first_failure is not None and actor.failure_strategy == "fail_fast":
                    raise first_failure

        if state.stage == "completed":
            if state.final_assistant_content is None:
                raise TeamPlanError("team_checkpoint_invalid: completed without result")
            return RuntimeResult(
                status="completed",
                content=state.final_assistant_content,
                state={},
            )

        self._ensure_team_active(snapshot, actor)
        if state.stage != "synthesizing":
            self._append_event(
                "team.synthesis.started",
                {
                    **team_fields,
                    "completed_members": len(completed),
                    "failed_members": len(failed),
                },
            )
            state = self._state_with_results(
                state,
                completed,
                failed,
                started,
                stage="synthesizing",
                active_invocations=(
                    TeamActiveInvocation(
                        task_id="synthesis",
                        member_agent_id=actor.supervisor.agent_id,
                        invocation_id=self._task_invocation_id(actor, "synthesis"),
                        checkpoint_status="running",
                    ),
                ),
            )
            state = self._save_team_state(state, model_budget)

        synthesis_invocation = next(
            (
                invocation
                for invocation in state.active_invocations
                if invocation.task_id == "synthesis"
                and invocation.member_agent_id == actor.supervisor.agent_id
            ),
            None,
        )
        if synthesis_invocation is None:
            raise TeamPlanError("team_checkpoint_invalid: synthesis invocation")
        synthesis_artifact_capability = self.gateway.register_artifact_capability(
            team_version_id=actor.version_id,
            member_agent_id=synthesis_invocation.member_agent_id,
            task_id=synthesis_invocation.task_id,
            invocation_id=synthesis_invocation.invocation_id,
        )

        synthesis_lines = [
            f"{item.task_id}: {item.content}"
            for item in sorted(completed.values(), key=lambda item: item.position)
        ]
        synthesis_lines.extend(
            f"{item.task_id} [{item.member_agent_id}] failed: {item.error_code}"
            for item in sorted(failed.values(), key=lambda item: item.position)
        )
        synthesis_messages = [
            *messages,
            {
                "role": "user",
                "content": (
                    "Synthesize these bounded Team task outcomes. Preserve failed "
                    "task details and do not claim full completion:\n"
                    + "\n".join(synthesis_lines)
                ),
            },
        ]
        supervisor_model, supervisor_tools = self._member_runtime_dependencies(
            snapshot,
            actor,
            actor.supervisor.agent_id,
            model_budget,
            legacy_model,
            legacy_tools,
        )
        graph = self.agent_factory.build(
            member_agent_snapshot(actor, actor.supervisor.agent_id),
            model=supervisor_model,
            tools=supervisor_tools,
            backend=ArtifactBackend(
                self.gateway,
                provenance={
                    "team_version_id": actor.version_id,
                    "member_agent_id": actor.supervisor.agent_id,
                    "task_id": "synthesis",
                    "invocation_id": synthesis_invocation.invocation_id,
                },
                capability=synthesis_artifact_capability,
            ),
        )
        result = self.runtime_adapter_type(graph).invoke(
            RuntimeState(
                run_id=request.run_id,
                messages=synthesis_messages,
                status="running",
                values={
                    "team_version_id": actor.version_id,
                    "team_task_id": "synthesis",
                    "team_member_agent_id": actor.supervisor.agent_id,
                    "team_task_invocation_id": synthesis_invocation.invocation_id,
                },
            ),
            metadata=metadata,
        )
        content = result.content
        if failed:
            failed_ids = ", ".join(
                item.task_id
                for item in sorted(failed.values(), key=lambda item: item.position)
            )
            content = (
                f"Partial completion: failed Team tasks: {failed_ids}.\n\n"
                f"{content}"
            )
        self._append_event(
            "team.synthesis.completed",
            {
                **team_fields,
                "partial": bool(failed),
                "failed_task_ids": [
                    item.task_id
                    for item in sorted(failed.values(), key=lambda item: item.position)
                ],
            },
        )
        self._append_event("runner.completed", {"status": result.status})
        state = self._state_with_results(
            state,
            completed,
            failed,
            started,
            stage="completed",
            active_task_id=None,
            active_member_agent_id=None,
            active_invocation_id=None,
            active_runtime_state=None,
            active_invocations=(),
        )
        state = state.model_copy(update={"final_assistant_content": content})
        self._save_team_state(state, model_budget)
        return RuntimeResult(status=result.status, content=content, state=result.state)

    def _create_team_plan(
        self,
        request,
        snapshot,
        actor,
        messages,
        metadata,
        model_budget,
    ) -> TeamPlan:
        limits = snapshot.payload.limits
        if snapshot.payload.schema_version != "5":
            plan = TeamPlan(
                tasks=tuple(
                    TeamTask(
                        id=f"member-{position + 1}",
                        member_id=member.agent_id,
                        objective=member.responsibility,
                        position=position,
                    )
                    for position, member in enumerate(actor.members)
                )
            )
            validate_team_plan(
                plan,
                actor,
                runner_max_subagents=limits.max_subagents,
            )
            return plan

        supervisor_model, _ = self._member_runtime_dependencies(
            snapshot,
            actor,
            actor.supervisor.agent_id,
            model_budget,
            None,
            None,
        )
        graph = self.agent_factory.build(
            member_agent_snapshot(actor, actor.supervisor.agent_id),
            model=supervisor_model,
            tools=[],
            backend=None,
        )
        member_lines = "\n".join(
            f"- {member.agent_id}: {member.responsibility}"
            for member in actor.members
        )
        planning_messages = [
            *messages,
            {
                "role": "user",
                "content": (
                    "Create the bounded Team execution plan. Return only one JSON "
                    "object matching {\"tasks\":[{\"id\":string,"
                    "\"member_id\":string,\"objective\":string,"
                    "\"depends_on\":[string],\"position\":integer,"
                    "\"output_contract\":{\"max_output_bytes\":integer}}]}. "
                    f"Use at most {actor.max_steps} tasks and only these members:\n"
                    f"{member_lines}"
                ),
            },
        ]
        result = self.runtime_adapter_type(graph).invoke(
            RuntimeState(
                run_id=request.run_id,
                messages=planning_messages,
                status="running",
                values={
                    "team_version_id": actor.version_id,
                    "team_stage": "planning",
                },
            ),
            metadata=metadata,
        )
        return parse_supervisor_plan(
            result.content,
            actor,
            runner_max_subagents=limits.max_subagents,
        )

    def _invoke_team_member(
        self,
        *,
        request,
        snapshot,
        actor,
        task,
        messages,
        metadata,
        model_budget,
        completed,
        legacy_model,
        legacy_tools,
        runtime_state,
        invocation,
        artifact_capability,
        checkpoint_callback,
    ):
        model, tools = self._member_runtime_dependencies(
            snapshot,
            actor,
            task.member_id,
            model_budget,
            legacy_model,
            legacy_tools,
        )
        graph = self.agent_factory.build(
            member_agent_snapshot(actor, task.member_id),
            model=model,
            tools=tools,
            backend=ArtifactBackend(
                self.gateway,
                provenance={
                    "team_version_id": actor.version_id,
                    "member_agent_id": task.member_id,
                    "task_id": task.id,
                    "invocation_id": invocation.invocation_id,
                },
                capability=artifact_capability,
            ),
        )
        dependency_content = "\n".join(
            f"{dependency}: {completed[dependency].content}"
            for dependency in task.depends_on
            if dependency in completed
        )
        task_messages = [
            *messages,
            {
                "role": "user",
                "content": (
                    f"Team task {task.id}: {task.objective}"
                    + (
                        f"\nCompleted dependencies:\n{dependency_content}"
                        if dependency_content
                        else ""
                    )
                ),
            },
        ]
        checkpoint_store = _TeamMemberCheckpointStore(
            runtime_state,
            checkpoint_callback,
        )
        return self.runtime_adapter_type(graph, checkpoint_store=checkpoint_store).invoke(
            RuntimeState(
                run_id=request.run_id,
                messages=task_messages,
                status="running",
                values={
                    "team_version_id": actor.version_id,
                    "team_task_id": task.id,
                    "team_member_agent_id": task.member_id,
                    "team_task_invocation_id": invocation.invocation_id,
                },
            ),
            metadata=metadata,
        )

    def _member_runtime_dependencies(
        self,
        snapshot,
        actor,
        member_id,
        model_budget,
        legacy_model,
        legacy_tools,
    ):
        if snapshot.payload.schema_version != "5":
            return legacy_model, legacy_tools
        member = next(
            item
            for item in (actor.supervisor, *actor.members)
            if item.agent_id == member_id
        )
        limits = snapshot.payload.limits
        model = GatewayChatModel(
            self.gateway,
            max_iterations=limits.max_iterations,
            max_tool_calls=limits.max_tool_calls,
            max_subagents=limits.max_subagents,
            max_output_bytes=limits.max_output_bytes,
            provider_id=member.model.provider_id,
            model_id=member.model.model,
            member_agent_id=member.agent_id,
            budget_state=model_budget,
        )
        tools = build_gateway_tools(
            snapshot.payload,
            self.gateway,
            allowed_tool_ids=(
                set(member.tool_ids) | set(member.knowledge_source_ids)
            ),
            member_agent_id=member.agent_id,
        )
        return model, tools

    def _load_team_state(
        self,
        run_id,
        actor,
        checkpoint_store,
        runner_max_subagents,
    ) -> TeamSchedulerState | None:
        raw = checkpoint_store.load_latest(run_id)
        if not isinstance(raw, dict) or raw.get("kind") != "team_scheduler":
            return None
        try:
            state = TeamSchedulerState.model_validate(raw)
        except ValidationError as error:
            raise TeamPlanError("team_checkpoint_invalid: invalid schema") from error
        if (
            state.snapshot_digest != checkpoint_store.snapshot_digest
            or state.team_version_id != actor.version_id
        ):
            raise TeamPlanError("team_checkpoint_invalid: immutable identity")
        validate_team_plan(
            state.plan,
            actor,
            runner_max_subagents=runner_max_subagents,
        )
        task_ids = {task.id for task in state.plan.tasks}
        settled_ids = {
            *(item.task_id for item in state.completed_results),
            *(item.task_id for item in state.failed_results),
        }
        if (
            not set(state.pending_task_ids) <= task_ids
            or not set(state.started_task_ids) <= task_ids
            or not settled_ids <= task_ids
            or len(settled_ids)
            != len(state.completed_results) + len(state.failed_results)
        ):
            raise TeamPlanError("team_checkpoint_invalid: task state")
        active_task_ids = [item.task_id for item in state.active_invocations]
        if (
            len(active_task_ids) != len(set(active_task_ids))
            or not set(active_task_ids) <= task_ids
            or not set(active_task_ids) <= set(state.started_task_ids)
            or set(active_task_ids) & settled_ids
        ):
            raise TeamPlanError("team_checkpoint_invalid: active task")
        return state

    def _save_team_state(
        self,
        state: TeamSchedulerState,
        budget: GatewayModelBudget,
    ) -> TeamSchedulerState:
        settled = {
            *(item.task_id for item in state.completed_results),
            *(item.task_id for item in state.failed_results),
        }
        pending = tuple(
            task.id for task in state.plan.tasks if task.id not in settled
        )
        updated = state.model_copy(
            update={
                "pending_task_ids": pending,
                "event_sequence": self._event_sequence,
                "checkpoint_revision": state.checkpoint_revision + 1,
                "budget": self._budget_snapshot(budget),
            }
        )
        self.gateway.save_checkpoint(
            "team-scheduler",
            updated.model_dump(mode="json"),
            f"checkpoint:team-scheduler:{updated.checkpoint_revision}",
        )
        return updated

    @staticmethod
    def _budget_snapshot(budget: GatewayModelBudget) -> TeamBudgetState:
        return TeamBudgetState(
            next_invocation_sequence=budget.next_invocation_sequence,
            tool_call_count=budget.tool_call_count,
            subagent_call_count=budget.subagent_call_count,
        )

    @staticmethod
    def _normalize_active_invocations(state: TeamSchedulerState) -> TeamSchedulerState:
        if state.active_invocations:
            return state
        legacy = (
            state.active_task_id,
            state.active_member_agent_id,
            state.active_invocation_id,
        )
        if legacy == (None, None, None):
            return state
        if any(value is None for value in legacy):
            raise TeamPlanError("team_checkpoint_invalid: active task")
        return state.model_copy(
            update={
                "active_invocations": (
                    TeamActiveInvocation(
                        task_id=state.active_task_id,
                        member_agent_id=state.active_member_agent_id,
                        invocation_id=state.active_invocation_id,
                        runtime_state=state.active_runtime_state,
                        checkpoint_status=(
                            "interrupted"
                            if state.stage == "waiting_approval"
                            else "running"
                        ),
                    ),
                ),
            }
        )

    @staticmethod
    def _recover_completed_invocations(active_invocations, task_by_id):
        recovered = []
        for invocation in active_invocations:
            task = task_by_id.get(invocation.task_id)
            if task is None or task.member_id != invocation.member_agent_id:
                raise TeamPlanError("team_checkpoint_invalid: active task")
            if invocation.runtime_state is None:
                raise _TeamRecoveryRequired()
            if invocation.checkpoint_status != "completed":
                continue
            messages = invocation.runtime_state.get("messages")
            if not isinstance(messages, list):
                raise _TeamRecoveryRequired()
            content = next(
                (
                    message.get("content")
                    for message in reversed(messages)
                    if isinstance(message, dict)
                    and message.get("role") == "assistant"
                    and isinstance(message.get("content"), str)
                    and message["content"].strip()
                ),
                None,
            )
            if content is None:
                raise _TeamRecoveryRequired()
            if len(content.encode("utf-8")) > task.output_contract.max_output_bytes:
                raise _TeamRecoveryRequired()
            recovered.append(
                TeamTaskResult(
                    task_id=task.id,
                    member_agent_id=task.member_id,
                    position=task.position,
                    content=content,
                )
            )
        return tuple(recovered)

    @staticmethod
    def _state_with_results(
        state,
        completed,
        failed,
        started,
        *,
        stage,
        active_task_id=None,
        active_member_agent_id=None,
        active_invocation_id=None,
        active_runtime_state=None,
        active_invocations=(),
    ):
        if active_invocations:
            legacy = active_invocations[0] if len(active_invocations) == 1 else None
            active_task_id = legacy.task_id if legacy is not None else None
            active_member_agent_id = (
                legacy.member_agent_id if legacy is not None else None
            )
            active_invocation_id = legacy.invocation_id if legacy is not None else None
            active_runtime_state = legacy.runtime_state if legacy is not None else None
        return state.model_copy(
            update={
                "stage": stage,
                "started_task_ids": tuple(sorted(started)),
                "completed_results": tuple(
                    sorted(completed.values(), key=lambda item: item.position)
                ),
                "failed_results": tuple(
                    sorted(failed.values(), key=lambda item: item.position)
                ),
                "active_task_id": active_task_id,
                "active_member_agent_id": active_member_agent_id,
                "active_invocation_id": active_invocation_id,
                "active_runtime_state": active_runtime_state,
                "active_invocations": active_invocations,
            }
        )

    @staticmethod
    def _blocked_team_tasks(plan, completed, failed):
        settled = set(completed) | set(failed)
        return tuple(
            task
            for task in sorted(plan.tasks, key=lambda item: item.position)
            if task.id not in settled
            and any(dependency in failed for dependency in task.depends_on)
        )

    @staticmethod
    def _task_invocation_id(actor, task_id):
        return f"team:{actor.version_id}:{task_id}"

    @staticmethod
    def _member_error_code(error):
        if isinstance(error, (RunnerGatewayModelError, RunnerGatewayClientError)):
            return error.code
        return "member_execution_failed"

    def _ensure_team_active(self, snapshot, actor) -> None:
        try:
            current = self.gateway.get_snapshot()
        except RunnerGatewayBusinessError as error:
            if error.code == "run_token_invalid":
                raise _TeamCancelled() from error
            raise
        if (
            current.snapshot_id != snapshot.snapshot_id
            or current.digest != snapshot.digest
            or not isinstance(current.payload.actor, PublishedTeamSnapshot)
            or current.payload.actor.version_id != actor.version_id
        ):
            raise TeamPlanError("team_checkpoint_invalid: immutable identity")

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
