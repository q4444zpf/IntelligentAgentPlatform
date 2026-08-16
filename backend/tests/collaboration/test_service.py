import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.collaboration.schemas import TeamCreateRequest, TeamDraft, TeamDraftUpdate, TeamMemberDraft
from app.collaboration.service import TeamService, TeamUnavailableError
from app.core.request_context import RequestContext
from app.db.base import Base


@pytest.fixture
def service():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield TeamService(session)


def admin(project="p1"):
    return RequestContext(user_id="u1", unit_id="unit-1", project_id=project, roles=frozenset({"project_admin"}))


def draft():
    return TeamDraft(
        supervisor=TeamMemberDraft(agent_id="supervisor", responsibility="coordinate", agent_definition_digest="a" * 64),
        members=[TeamMemberDraft(agent_id="member", responsibility="review", agent_definition_digest="b" * 64)],
        max_steps=4, max_parallel_members=1, timeout_seconds=60,
    )


def test_team_publish_enable_and_resolve_uses_immutable_version(service):
    context = admin()
    team = service.create(context, TeamCreateRequest(name="联合研判"))
    service.save_draft(context, team.id, TeamDraftUpdate(revision=1, draft=draft()))
    version = service.publish(context, team.id)
    assert version.version == 1

    with pytest.raises(TeamUnavailableError):
        service.resolve_for_run(context, team.id)
    service.set_enabled(context, team.id, True)
    resolved = service.resolve_for_run(context, team.id)
    assert resolved.version_id == version.id
    assert len(resolved.definition_digest) == 64


def test_team_resolution_is_project_scoped(service):
    context = admin()
    team = service.create(context, TeamCreateRequest(name="联合研判"))
    service.save_draft(context, team.id, TeamDraftUpdate(revision=1, draft=draft()))
    service.publish(context, team.id)
    service.set_enabled(context, team.id, True)
    with pytest.raises(TeamUnavailableError):
        service.resolve_for_run(admin("other-project"), team.id)
