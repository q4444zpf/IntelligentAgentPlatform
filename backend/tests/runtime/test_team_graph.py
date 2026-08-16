import pytest

from app.runtime.execution_snapshot import PublishedTeamSnapshot, SnapshotTeamMember
from app.runtime.team_graph import TeamPlan, TeamPlanError, TeamTask, member_agent_snapshot, schedule_ready_tasks, validate_team_plan


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


def test_schedule_ready_tasks_rejects_zero_parallelism(snapshot):
    with pytest.raises(ValueError, match="team_limit_exceeded"):
        schedule_ready_tasks(TeamPlan(tasks=()), set(), max_parallel_members=0)


def test_member_agent_snapshot_is_factory_compatible(snapshot):
    member = member_agent_snapshot(snapshot, "member")
    assert member.agent_id == "member"
    assert "review" in member.system_prompt
