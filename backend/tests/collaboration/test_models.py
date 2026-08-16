import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.collaboration.models import Team, TeamVersion, TeamVersionMember
from app.db.base import Base


def test_team_model_enforces_project_name_and_positive_runtime_limits():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Team(id="team-1", unit_id="u1", project_id="p1", name="联合研判"),
                Team(id="team-2", unit_id="u1", project_id="p1", name="联合研判"),
            ]
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

        session.add(
            TeamVersion(
                id="draft-1",
                team_id="team-1",
                version=0,
                status="draft",
                definition={},
                max_steps=0,
                max_parallel_members=1,
                timeout_seconds=1,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()


def test_team_version_members_require_valid_roles_and_deterministic_positions():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        team = Team(id="team-1", unit_id="u1", project_id="p1", name="联合研判")
        version = TeamVersion(
            id="draft-1",
            team_id="team-1",
            version=0,
            status="draft",
            definition={},
            max_steps=1,
            max_parallel_members=1,
            timeout_seconds=1,
        )
        session.add_all([team, version])
        session.flush()
        session.add(
            TeamVersionMember(
                id="member-1",
                team_version_id="draft-1",
                agent_id="forecast-agent",
                agent_definition_digest="a" * 64,
                agent_definition={},
                role="invalid",
                responsibility="forecast",
                position=0,
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    with Session(engine) as session:
        version = session.scalar(select(TeamVersion).where(TeamVersion.id == "draft-1"))
        assert version is None
