from __future__ import annotations

import json
from collections import defaultdict, deque
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .execution_snapshot import PublishedTeamSnapshot
from .deepagents_factory import (
    PublishedAgentSnapshot,
    PublishedSkillSnapshot,
    PublishedToolSnapshot,
)


class TeamPlanError(ValueError):
    pass


class TeamLimitError(TeamPlanError):
    pass


class TeamTaskOutputContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_output_bytes: int = Field(default=65_536, gt=0, le=4 * 1024 * 1024)


class TeamTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    member_id: str = Field(min_length=1, max_length=128)
    objective: str = Field(min_length=1, max_length=4000)
    depends_on: tuple[str, ...] = ()
    position: int = Field(ge=0)
    output_contract: TeamTaskOutputContract = Field(
        default_factory=TeamTaskOutputContract
    )


class TeamPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tasks: tuple[TeamTask, ...]


class TeamTaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    member_agent_id: str
    position: int = Field(ge=0)
    content: str


class TeamTaskFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    member_agent_id: str
    position: int = Field(ge=0)
    error_code: str


class TeamBudgetState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    next_invocation_sequence: int = Field(ge=0)
    tool_call_count: int = Field(ge=0)
    subagent_call_count: int = Field(ge=0)


class TeamActiveInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    member_agent_id: str
    invocation_id: str
    runtime_state: dict[str, Any] | None = None
    checkpoint_status: Literal["running", "interrupted", "completed"]
    approval_id: str | None = None


class TeamSchedulerState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["team_scheduler"] = "team_scheduler"
    stage: Literal[
        "executing",
        "waiting_approval",
        "synthesizing",
        "completed",
    ]
    snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    team_version_id: str
    plan: TeamPlan
    pending_task_ids: tuple[str, ...] = ()
    started_task_ids: tuple[str, ...] = ()
    completed_results: tuple[TeamTaskResult, ...] = ()
    failed_results: tuple[TeamTaskFailure, ...] = ()
    active_task_id: str | None = None
    active_member_agent_id: str | None = None
    active_invocation_id: str | None = None
    active_runtime_state: dict[str, Any] | None = None
    active_invocations: tuple[TeamActiveInvocation, ...] = ()
    final_assistant_content: str | None = None
    event_sequence: int = Field(default=0, ge=0)
    checkpoint_revision: int = Field(default=0, ge=0)
    budget: TeamBudgetState


def member_agent_snapshot(snapshot: PublishedTeamSnapshot, member_id: str) -> PublishedAgentSnapshot:
    """Build the immutable factory input for one Team member."""
    candidates = [snapshot.supervisor, *snapshot.members]
    member = next((item for item in candidates if item.agent_id == member_id), None)
    if member is None:
        raise TeamPlanError("team_plan_invalid: unknown member")
    if member.agent is None and member.model is None:
        return PublishedAgentSnapshot(
            agent_id=member.agent_id,
            name=member.agent_id,
            system_prompt=(
                f"You are the {member.role} member. "
                f"Responsibility: {member.responsibility}"
            ),
            context_prompt="",
            tools=(),
        )
    if member.agent is None or member.model is None:
        raise TeamPlanError("team_plan_invalid: incomplete member snapshot")
    return PublishedAgentSnapshot(
        agent_id=member.agent_id,
        name=member.agent.name,
        system_prompt=member.agent.system_prompt,
        context_prompt=member.agent.context_prompt,
        tools=tuple(
            PublishedToolSnapshot(
                name=tool.tool_id,
                description=tool.description,
                input_schema=tool.input_schema,
                published=tool.published,
                enabled=tool.enabled,
            )
            for tool in member.tools
        ),
        skills=tuple(
            PublishedSkillSnapshot(name=skill.name, content=skill.content)
            for skill in member.skills
        ),
        knowledge_source_ids=member.knowledge_source_ids,
    )


def validate_team_plan(
    plan: TeamPlan,
    snapshot: PublishedTeamSnapshot,
    *,
    runner_max_subagents: int | None = None,
) -> None:
    allowed = {snapshot.supervisor.agent_id, *(member.agent_id for member in snapshot.members)}
    if len(plan.tasks) > snapshot.max_steps:
        raise TeamLimitError("team_limit_exceeded: max_steps")
    if plan.tasks and runner_max_subagents is not None and runner_max_subagents <= 0:
        raise TeamLimitError("team_limit_exceeded: max_subagents")
    ids = {task.id for task in plan.tasks}
    if len(ids) != len(plan.tasks):
        raise TeamPlanError("team_plan_invalid: duplicate task")
    if any(task.member_id not in allowed for task in plan.tasks):
        raise TeamPlanError("team_plan_invalid: unknown member")
    if any(dependency not in ids for task in plan.tasks for dependency in task.depends_on):
        raise TeamPlanError("team_plan_invalid: unknown dependency")
    if any(
        len(task.depends_on) != len(set(task.depends_on))
        for task in plan.tasks
    ):
        raise TeamPlanError("team_plan_invalid: duplicate dependency")
    graph = defaultdict(list)
    indegree = {task.id: 0 for task in plan.tasks}
    for task in plan.tasks:
        for dependency in task.depends_on:
            graph[dependency].append(task.id)
            indegree[task.id] += 1
    queue = deque(task_id for task_id, degree in indegree.items() if degree == 0)
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for successor in graph[current]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                queue.append(successor)
    if visited != len(plan.tasks):
        raise TeamPlanError("team_plan_invalid: dependency cycle")
    positions = [task.position for task in plan.tasks]
    if len(positions) != len(set(positions)):
        raise TeamPlanError("team_plan_invalid: duplicate position")
    width = parallel_width(plan)
    if width > snapshot.max_parallel_members:
        raise TeamLimitError("team_limit_exceeded: max_parallel_members")
    if runner_max_subagents is not None and width > runner_max_subagents:
        raise TeamLimitError("team_limit_exceeded: max_subagents")


def parse_supervisor_plan(
    value: str | dict[str, Any],
    snapshot: PublishedTeamSnapshot,
    *,
    runner_max_subagents: int,
) -> TeamPlan:
    """Parse an untrusted supervisor response through the platform schema."""
    try:
        raw = json.loads(value) if isinstance(value, str) else value
        plan = TeamPlan.model_validate(raw)
    except (json.JSONDecodeError, TypeError, ValidationError) as error:
        raise TeamPlanError("team_plan_invalid: invalid schema") from error
    validate_team_plan(
        plan,
        snapshot,
        runner_max_subagents=runner_max_subagents,
    )
    return plan


def parallel_width(plan: TeamPlan) -> int:
    settled: set[str] = set()
    maximum = 0
    while len(settled) < len(plan.tasks):
        ready = [
            task
            for task in plan.tasks
            if task.id not in settled and set(task.depends_on) <= settled
        ]
        if not ready:
            break
        maximum = max(maximum, len({task.member_id for task in ready}))
        settled.update(task.id for task in ready)
    return maximum


def schedule_ready_tasks(
    plan: TeamPlan,
    completed: set[str],
    *,
    failed: set[str] | None = None,
    max_parallel_members: int,
) -> tuple[TeamTask, ...]:
    """Return the next deterministic batch whose dependencies are complete."""
    if max_parallel_members <= 0:
        raise TeamLimitError("team_limit_exceeded: max_parallel_members")
    validate_ids = {task.id for task in plan.tasks}
    failed = failed or set()
    if not completed <= validate_ids or not failed <= validate_ids:
        raise TeamPlanError("team_plan_invalid: unknown settled task")
    settled = completed | failed
    ready = [
        task for task in plan.tasks
        if task.id not in settled and set(task.depends_on) <= completed
    ]
    ready.sort(key=lambda task: (task.position, task.id))
    selected: list[TeamTask] = []
    members: set[str] = set()
    for task in ready:
        if task.member_id in members:
            continue
        selected.append(task)
        members.add(task.member_id)
        if len(selected) >= max_parallel_members:
            break
    return tuple(selected)
