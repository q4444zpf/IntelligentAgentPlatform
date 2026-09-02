import pytest
from pydantic import ValidationError

from app.runtime.execution_snapshot import PublishedTeamSnapshot, SnapshotTeamMember
from app.runtime import team_graph
from app.runtime.team_graph import TeamPlan, TeamLimitError, TeamPlanError, TeamTask, member_agent_snapshot, schedule_ready_tasks, validate_team_plan


@pytest.fixture
def snapshot():
    return PublishedTeamSnapshot(
        id="team-1", version_id="version-1", version=1, definition_digest="a" * 64,
        supervisor=SnapshotTeamMember(agent_id="supervisor", role="supervisor", responsibility="coordinate"),
        members=(SnapshotTeamMember(agent_id="member", role="member", responsibility="review"),),
        max_steps=4, max_parallel_members=2, timeout_seconds=60,
        failure_strategy="fail_fast", name="Team", description="", runtime_form="common",
        language="en-US", system_prompt="", context_prompt="", approval_policy="never",
    )


def test_team_plan_rejects_unknown_member(snapshot):
    with pytest.raises(TeamPlanError, match="unknown member"):
        validate_team_plan(TeamPlan(tasks=(TeamTask(id="task", member_id="other", objective="x", position=0),)), snapshot)


def test_team_plan_rejects_dependency_cycle(snapshot):
    with pytest.raises(TeamPlanError, match="dependency cycle"):
        validate_team_plan(TeamPlan(tasks=(
            TeamTask(id="a", member_id="member", objective="a", depends_on=("b",), position=0),
            TeamTask(id="b", member_id="member", objective="b", depends_on=("a",), position=1),
        )), snapshot)


def test_team_plan_schema_rejects_extra_fields_and_duplicate_ids_or_positions(snapshot):
    with pytest.raises(ValidationError, match="extra_forbidden"):
        TeamPlan.model_validate({
            "tasks": [{
                "id": "a",
                "member_id": "member",
                "objective": "review",
                "position": 0,
                "untrusted": True,
            }],
        })
    with pytest.raises(TeamPlanError, match="duplicate task"):
        validate_team_plan(TeamPlan(tasks=(
            TeamTask(id="a", member_id="member", objective="one", position=0),
            TeamTask(id="a", member_id="member", objective="two", position=1),
        )), snapshot)
    with pytest.raises(TeamPlanError, match="duplicate position"):
        validate_team_plan(TeamPlan(tasks=(
            TeamTask(id="a", member_id="member", objective="one", position=0),
            TeamTask(id="b", member_id="member", objective="two", position=0),
        )), snapshot)


def test_team_plan_rejects_max_steps_and_runner_subagent_ceiling(snapshot):
    tasks = tuple(
        TeamTask(
            id=f"task-{position}",
            member_id="member",
            objective="review",
            position=position,
        )
        for position in range(5)
    )
    with pytest.raises(TeamLimitError, match="max_steps"):
        validate_team_plan(TeamPlan(tasks=tasks), snapshot)
    with pytest.raises(TeamLimitError, match="max_subagents"):
        validate_team_plan(
            TeamPlan(tasks=(tasks[0],)),
            snapshot,
            runner_max_subagents=0,
        )


def test_team_plan_rejects_parallel_width_above_team_or_runner_ceiling(snapshot):
    plan = TeamPlan(tasks=(
        TeamTask(id="supervisor-task", member_id="supervisor", objective="one", position=0),
        TeamTask(id="member-task", member_id="member", objective="two", position=1),
    ))

    constrained = snapshot.model_copy(update={"max_parallel_members": 1})
    with pytest.raises(TeamLimitError, match="max_parallel_members"):
        validate_team_plan(plan, constrained, runner_max_subagents=2)
    with pytest.raises(TeamLimitError, match="max_subagents"):
        validate_team_plan(plan, snapshot, runner_max_subagents=1)


def test_supervisor_plan_requires_a_bounded_output_contract(snapshot):
    plan = team_graph.parse_supervisor_plan(
        {
            "tasks": [{
                "id": "review",
                "member_id": "member",
                "objective": "Review flood forecast",
                "depends_on": [],
                "position": 0,
                "output_contract": {"max_output_bytes": 1024},
            }],
        },
        snapshot,
        runner_max_subagents=1,
    )

    assert plan.tasks[0].output_contract.max_output_bytes == 1024
    with pytest.raises(TeamPlanError, match="invalid schema"):
        team_graph.parse_supervisor_plan(
            {
                "tasks": [{
                    "id": "invalid-output",
                    "member_id": "member",
                    "objective": "Review flood forecast",
                    "depends_on": [],
                    "position": 0,
                    "output_contract": {"max_output_bytes": 0},
                }],
            },
            snapshot,
            runner_max_subagents=1,
        )


def test_platform_plan_parser_accepts_only_typed_validated_json(snapshot):
    parser = getattr(team_graph, "parse_supervisor_plan", None)
    assert callable(parser), "platform supervisor plan parser is missing"

    plan = parser(
        '{"tasks":[{"id":"review","member_id":"member",'
        '"objective":"Review flood forecast","depends_on":[],"position":0}]}',
        snapshot,
        runner_max_subagents=1,
    )

    assert plan.tasks[0].id == "review"
    with pytest.raises(TeamPlanError, match="team_plan_invalid"):
        parser("not-json", snapshot, runner_max_subagents=1)


def test_schedule_ready_tasks_is_deterministic_and_bounded(snapshot):
    plan = TeamPlan(tasks=(
        TeamTask(id="b", member_id="member", objective="b", position=1),
        TeamTask(id="a", member_id="supervisor", objective="a", position=0),
        TeamTask(id="c", member_id="member", objective="c", depends_on=("a",), position=2),
    ))
    validate_team_plan(plan, snapshot)
    batch = schedule_ready_tasks(plan, set(), max_parallel_members=2)
    assert [task.id for task in batch] == ["a", "b"]
    assert [task.id for task in schedule_ready_tasks(plan, {"a", "b"}, max_parallel_members=2)] == ["c"]


def test_schedule_ready_tasks_does_not_dequeue_failed_dependencies(snapshot):
    plan = TeamPlan(tasks=(
        TeamTask(id="a", member_id="supervisor", objective="a", position=0),
        TeamTask(
            id="b",
            member_id="member",
            objective="b",
            depends_on=("a",),
            position=1,
        ),
    ))

    assert schedule_ready_tasks(
        plan,
        set(),
        failed={"a"},
        max_parallel_members=2,
    ) == ()


def test_schedule_ready_tasks_rejects_zero_parallelism(snapshot):
    with pytest.raises(ValueError, match="team_limit_exceeded"):
        schedule_ready_tasks(TeamPlan(tasks=()), set(), max_parallel_members=0)


def test_member_agent_snapshot_is_factory_compatible(snapshot):
    member = member_agent_snapshot(snapshot, "member")
    assert member.agent_id == "member"
    assert "review" in member.system_prompt
