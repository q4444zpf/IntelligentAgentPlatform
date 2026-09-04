import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.collaboration.repository import (
    TeamDraftConflictError,
    TeamRepository,
    TeamVersionImmutableError,
)
from app.db.base import Base


def valid_definition():
    return {
        "supervisor": {
            "agent_id": "supervisor-agent",
            "agent_version_id": "a" * 64,
            "agent_definition": {"name": "supervisor"},
            "responsibility": "coordinate",
            "tool_ids": ["forecast.read"],
            "skill_names": [],
            "knowledge_source_ids": [],
        },
        "members": [
            {
                "agent_id": "member-agent",
                "agent_version_id": "b" * 64,
                "agent_definition": {"name": "member"},
                "responsibility": "review",
                "tool_ids": ["forecast.read"],
                "skill_names": [],
                "knowledge_source_ids": [],
            }
        ],
        "tool_ids": ["forecast.read"],
        "skill_names": [],
        "knowledge_source_ids": [],
        "max_steps": 6,
        "max_parallel_members": 1,
        "timeout_seconds": 60,
        "failure_strategy": "fail_fast",
        "approval_policy_id": None,
    }


@pytest.fixture
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_team_requires_one_supervisor_and_published_versions_are_immutable(session):
    repository = TeamRepository(session)
    team = repository.create(unit_id="u1", project_id="p1", name="联合研判", created_by="user-1")
    repository.save_draft(team.id, expected_revision=1, definition=valid_definition())
    published = repository.publish(
        team.id,
        expected_revision=2,
        definition_digest="a" * 64,
        published_by="user-1",
    )

    assert published.version == 1
    assert published.status == "published"
    assert [(member.role, member.position) for member in published.members] == [
        ("supervisor", 0),
        ("member", 1),
    ]
    with pytest.raises(TeamVersionImmutableError):
        repository.replace_published_definition(published.id, valid_definition())


def test_repository_hides_cross_project_team(session):
    team = TeamRepository(session).create(unit_id="u1", project_id="p1", name="A", created_by="x")

    assert TeamRepository(session).get_scoped("u1", "p2", team.id) is None
    assert TeamRepository(session).list_scoped("u1", "p2") == []


def test_save_draft_rejects_stale_revision(session):
    repository = TeamRepository(session)
    team = repository.create(unit_id="u1", project_id="p1", name="A", created_by="x")

    with pytest.raises(TeamDraftConflictError):
        repository.save_draft(team.id, expected_revision=2, definition=valid_definition())
