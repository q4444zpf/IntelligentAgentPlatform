from __future__ import annotations

from collections import defaultdict, deque

from pydantic import BaseModel, Field

from .execution_snapshot import PublishedTeamSnapshot


class TeamPlanError(ValueError):
    pass


class TeamLimitError(TeamPlanError):
    pass


class TeamTask(BaseModel):
    id: str
    member_id: str
    objective: str = Field(min_length=1, max_length=4000)
    depends_on: tuple[str, ...] = ()
    position: int = Field(ge=0)


class TeamPlan(BaseModel):
    tasks: tuple[TeamTask, ...]


def validate_team_plan(plan: TeamPlan, snapshot: PublishedTeamSnapshot) -> None:
    allowed = {snapshot.supervisor.agent_id, *(member.agent_id for member in snapshot.members)}
    if len(plan.tasks) > snapshot.max_steps:
        raise TeamLimitError("team_limit_exceeded: max_steps")
    ids = {task.id for task in plan.tasks}
    if len(ids) != len(plan.tasks):
        raise TeamPlanError("team_plan_invalid: duplicate task")
    if any(task.member_id not in allowed for task in plan.tasks):
        raise TeamPlanError("team_plan_invalid: unknown member")
    if any(dependency not in ids for task in plan.tasks for dependency in task.depends_on):
        raise TeamPlanError("team_plan_invalid: unknown dependency")
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


def parallel_width(plan: TeamPlan) -> int:
    return max((sum(not task.depends_on for task in plan.tasks), 0))


def schedule_ready_tasks(
    plan: TeamPlan, completed: set[str], *, max_parallel_members: int
) -> tuple[TeamTask, ...]:
    """Return the next deterministic batch whose dependencies are complete."""
    validate_ids = {task.id for task in plan.tasks}
    if not completed <= validate_ids:
        raise TeamPlanError("team_plan_invalid: unknown completed task")
    ready = [
        task for task in plan.tasks
        if task.id not in completed and set(task.depends_on) <= completed
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
