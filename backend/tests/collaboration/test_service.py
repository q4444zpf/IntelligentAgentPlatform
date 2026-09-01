import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.collaboration.schemas import TeamCreateRequest, TeamDraft, TeamDraftUpdate, TeamMemberDraft, TeamMetadataUpdate
from app.collaboration.repository import TeamNotFoundError
from app.collaboration.service import TeamPermissionError, TeamService, TeamUnavailableError
from app.core.request_context import RequestContext
from app.db.base import Base
from app.identity.schemas import AuthorizationContext, PermissionGrant


@pytest.fixture
def service():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield TeamService(session)


def admin(project="p1"):
    return scoped_context(
        project_grant("collaboration.read", project),
        project_grant("collaboration.manage", project),
        project_grant("collaboration.run", project),
        project=project,
        roles=("project_admin",),
    )


def reader(project="p1"):
    return scoped_context(
        project_grant("collaboration.read", project),
        project=project,
    )


def scoped_context(*permissions: PermissionGrant, project="p1", roles=("viewer",)):
    authorization = AuthorizationContext(
        session_id="test-session",
        user_id="u1",
        unit_id="unit-1",
        current_project_id=project,
        auth_method="dev_test",
        authorization_version=1,
        role_codes=roles,
        grants=permissions,
    )
    return RequestContext(
        user_id="u1",
        unit_id="unit-1",
        project_id=project,
        roles=frozenset({"user"}),
        authorization_context=authorization,
    )


def project_grant(permission: str, project="p1"):
    return PermissionGrant(permission, "project", frozenset({project}), None)


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


def test_team_version_history_exposes_only_immutable_published_versions(service):
    context = admin()
    team = service.create(context, TeamCreateRequest(name="联合研判"))
    service.save_draft(context, team.id, TeamDraftUpdate(revision=1, draft=draft()))
    published = service.publish(context, team.id)

    versions = service.list_versions(reader(), team.id)

    assert [version.id for version in versions] == [published.id]
    assert versions[0].status == "published"


def test_team_draft_definition_is_visible_only_to_managers(service):
    context = admin()
    team = service.create(context, TeamCreateRequest(name="联合研判"))
    service.save_draft(context, team.id, TeamDraftUpdate(revision=1, draft=draft()))

    saved = service.get_version(context, team.id, 0)

    assert saved.status == "draft"
    assert saved.definition["supervisor"]["agent_id"] == "supervisor"
    with pytest.raises(TeamPermissionError):
        service.get_version(reader(), team.id, 0)


def test_team_version_lookup_is_project_scoped(service):
    context = admin()
    team = service.create(context, TeamCreateRequest(name="联合研判"))
    service.save_draft(context, team.id, TeamDraftUpdate(revision=1, draft=draft()))
    service.publish(context, team.id)

    with pytest.raises(TeamNotFoundError):
        service.list_versions(admin("other-project"), team.id)


def test_project_scoped_manage_grant_can_create_and_publish_without_admin_role(service):
    context = scoped_context(project_grant("collaboration.manage"))

    team = service.create(context, TeamCreateRequest(name="联合研判"))
    service.save_draft(context, team.id, TeamDraftUpdate(revision=1, draft=draft()))

    published = service.publish(context, team.id)

    assert published.team_id == team.id


def test_team_operations_require_the_exact_project_permission(service):
    no_manage = scoped_context(project_grant("collaboration.read"))

    with pytest.raises(TeamPermissionError, match="collaboration.manage"):
        service.create(no_manage, TeamCreateRequest(name="联合研判"))


def test_grant_for_another_project_cannot_read_or_run_a_team(service):
    manager = scoped_context(
        project_grant("collaboration.manage"),
        project_grant("collaboration.run"),
    )
    team = service.create(manager, TeamCreateRequest(name="联合研判"))
    service.save_draft(manager, team.id, TeamDraftUpdate(revision=1, draft=draft()))
    service.publish(manager, team.id)
    service.set_enabled(manager, team.id, True)
    other_project_grants = scoped_context(
        project_grant("collaboration.read", "p2"),
        project_grant("collaboration.run", "p2"),
    )

    with pytest.raises(TeamPermissionError, match="collaboration.read"):
        service.get(other_project_grants, team.id)
    with pytest.raises(TeamPermissionError, match="collaboration.run"):
        service.resolve_for_run(other_project_grants, team.id)


def test_team_lifecycle_mutations_record_project_scoped_audit_events(service):
    context = scoped_context(project_grant("collaboration.manage"), roles=("custom_manager",))
    team = service.create(context, TeamCreateRequest(name="联合研判"))
    service.update(context, team.id, TeamMetadataUpdate(name="防洪会商", description=""))
    service.save_draft(context, team.id, TeamDraftUpdate(revision=1, draft=draft()))
    published = service.publish(context, team.id)
    service.set_enabled(context, team.id, True)

    events = list(service.session.scalars(select(AuditEvent).order_by(AuditEvent.action)))

    assert {(event.action, event.resource_type, event.resource_id) for event in events} == {
        ("resource.created", "team", team.id),
        ("resource.updated", "team", team.id),
        ("resource.published", "team", team.id),
        ("resource.enabled", "team", team.id),
    }
    published_event = next(event for event in events if event.action == "resource.published")
    assert published_event.metadata_json == {"version_id": published.id}
    assert all(event.project_id == "p1" for event in events)
    assert all(event.actor_roles_json == ["custom_manager"] for event in events)
