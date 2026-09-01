from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.agents.schemas import AgentInfo
from app.agents.service import AgentNotFoundError
from app.audit.models import AuditEvent
from app.collaboration.schemas import TeamCreateRequest, TeamDraft, TeamDraftUpdate, TeamMemberDraft, TeamMetadataUpdate
from app.collaboration.repository import TeamDefinitionValidationError, TeamNotFoundError
from app.collaboration.service import TeamPermissionError, TeamService, TeamUnavailableError
from app.core.request_context import RequestContext
from app.db.base import Base
from app.identity.schemas import AuthorizationContext, PermissionGrant
from app.skills.service import SkillNotFoundError
from app.tools.service import ToolNotFoundError, ToolValidationError


def agent(agent_id: str, *, enabled: bool = True, tool_ids=None, skill_names=None):
    return AgentInfo(
        id=agent_id,
        name=f"{agent_id} name",
        description=f"{agent_id} description",
        runtime_form="common",
        language="zh-CN",
        provider_id="provider-1",
        model=f"{agent_id}-model",
        system_prompt=f"{agent_id} system prompt",
        context_prompt=f"{agent_id} context prompt",
        approval_policy="control_commands",
        skill_names=skill_names or [],
        tool_ids=tool_ids or [],
        enabled=enabled,
        pinned=False,
        is_builtin=False,
        is_default=False,
        startup_status="ready" if enabled else "disabled",
        workspace_dir=f"/workspace/{agent_id}",
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        updated_at=datetime(2026, 9, 1, tzinfo=UTC),
    )


def tool(tool_id: str, *, enabled: bool = True):
    return SimpleNamespace(
        tool_id=tool_id,
        version="1",
        name=tool_id,
        description=f"{tool_id} description",
        input_schema={"type": "object"},
        published=True,
        enabled=enabled,
        source_available=True,
    )


class StaticToolService:
    def __init__(self, tools):
        self.tools = {item.tool_id: item for item in tools}

    def resolve_bindable(self, tool_ids):
        resolved = []
        for tool_id in tool_ids:
            item = self.tools.get(tool_id)
            if item is None:
                raise ToolNotFoundError(tool_id)
            if not item.published or not item.enabled or not item.source_available:
                raise ToolValidationError(f"Tool '{tool_id}' is not available for binding")
            resolved.append(item)
        return resolved


class StaticSkillService:
    def __init__(self, skills):
        self.skills = {item.name: item for item in skills}

    def get(self, name):
        item = self.skills.get(name)
        if item is None:
            raise SkillNotFoundError(name)
        return item


class StaticAgentService:
    def __init__(self, agents=None, tools=None, skills=None):
        self.agents = agents or {
            "supervisor": agent(
                "supervisor", tool_ids=["forecast.read"], skill_names=["forecast"]
            ),
            "member": agent(
                "member", tool_ids=["forecast.read"], skill_names=["forecast"]
            ),
        }
        self.tool_service = StaticToolService(
            tools or [tool("forecast.read"), tool("review.write")]
        )
        self.skill_service = StaticSkillService(
            skills or [SimpleNamespace(name="forecast", enabled=True, version="1")]
        )

    def get(self, agent_id):
        try:
            return self.agents[agent_id]
        except KeyError as error:
            raise AgentNotFoundError(agent_id) from error


@pytest.fixture
def service():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield TeamService(session, agent_service=StaticAgentService())


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


def unit_grant(permission: str):
    return PermissionGrant(permission, "unit", frozenset(), None)


def draft():
    return TeamDraft(
        supervisor=TeamMemberDraft(agent_id="supervisor", responsibility="coordinate"),
        members=[TeamMemberDraft(agent_id="member", responsibility="review")],
        max_steps=4, max_parallel_members=1, timeout_seconds=60,
    )


@pytest.mark.parametrize("forged_field", ["agent_definition", "agent_definition_digest"])
def test_team_member_draft_rejects_client_snapshot_fields(forged_field):
    payload = {
        "agent_id": "supervisor",
        "responsibility": "coordinate",
        forged_field: {"system_prompt": "forged"}
        if forged_field == "agent_definition"
        else "a" * 64,
    }

    with pytest.raises(ValidationError, match=forged_field):
        TeamMemberDraft.model_validate(payload)


def test_publish_captures_complete_canonical_agent_definitions(service):
    context = admin()
    team = service.create(context, TeamCreateRequest(name="联合研判"))
    requested = draft().model_copy(
        update={
            "tool_ids": ["forecast.read"],
            "skill_names": ["forecast"],
            "supervisor": draft().supervisor.model_copy(
                update={"tool_ids": ["forecast.read"], "skill_names": ["forecast"]}
            ),
        }
    )
    service.save_draft(
        context, team.id, TeamDraftUpdate(revision=1, draft=requested)
    )

    published = service.publish(context, team.id)

    supervisor = next(member for member in published.members if member.role == "supervisor")
    stored = service.repository.get_version_by_id(published.id)
    stored_supervisor = next(member for member in stored.members if member.role == "supervisor")
    assert supervisor.agent_definition_digest == stored_supervisor.agent_definition_digest
    assert len(supervisor.agent_definition_digest) == 64
    assert stored_supervisor.agent_definition == {
        "approval_policy": "control_commands",
        "context_prompt": "supervisor context prompt",
        "description": "supervisor description",
        "id": "supervisor",
        "language": "zh-CN",
        "model": "supervisor-model",
        "name": "supervisor name",
        "knowledge_source_ids": [],
        "provider_id": "provider-1",
        "runtime_form": "common",
        "skill_names": ["forecast"],
        "system_prompt": "supervisor system prompt",
        "tool_ids": ["forecast.read"],
        "tools": [
            {
                "description": "forecast.read description",
                "enabled": True,
                "input_schema": {"type": "object"},
                "name": "forecast.read",
                "published": True,
                "source_available": True,
                "tool_id": "forecast.read",
                "version": "1",
            }
        ],
    }


@pytest.mark.parametrize("unavailable", ["missing", "disabled", "cross_project"])
def test_publish_rejects_unavailable_agents(unavailable):
    agent_service = StaticAgentService()
    if unavailable == "missing":
        del agent_service.agents["member"]
    elif unavailable == "disabled":
        agent_service.agents["member"] = agent("member", enabled=False)
    else:
        scoped = agent("member").model_dump()
        agent_service.agents["member"] = SimpleNamespace(
            **scoped, unit_id="unit-1", project_id="other-project"
        )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        service = TeamService(session, agent_service=agent_service)
        context = admin()
        team = service.create(context, TeamCreateRequest(name="联合研判"))
        service.save_draft(
            context, team.id, TeamDraftUpdate(revision=1, draft=draft())
        )

        with pytest.raises(TeamDefinitionValidationError, match="Agent 'member'"):
            service.publish(context, team.id)


def test_publish_rejects_whitelists_outside_team_agent_and_availability_intersection():
    agent_service = StaticAgentService()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        service = TeamService(session, agent_service=agent_service)
        context = admin()
        team = service.create(context, TeamCreateRequest(name="联合研判"))
        invalid = draft().model_copy(
            update={
                "tool_ids": ["review.write"],
                "members": [
                    draft().members[0].model_copy(
                        update={"tool_ids": ["review.write"]}
                    )
                ],
            }
        )
        service.save_draft(
            context, team.id, TeamDraftUpdate(revision=1, draft=invalid)
        )

        with pytest.raises(TeamDefinitionValidationError, match="review.write"):
            service.publish(context, team.id)


def test_publish_rejects_agent_bindings_that_are_no_longer_available():
    agent_service = StaticAgentService(
        agents={
            "supervisor": agent("supervisor", tool_ids=["disabled.tool"]),
            "member": agent("member"),
        },
        tools=[tool("disabled.tool", enabled=False)],
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        service = TeamService(session, agent_service=agent_service)
        context = admin()
        team = service.create(context, TeamCreateRequest(name="联合研判"))
        service.save_draft(
            context, team.id, TeamDraftUpdate(revision=1, draft=draft())
        )

        with pytest.raises(TeamDefinitionValidationError, match="disabled.tool"):
            service.publish(context, team.id)


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
    assert "agent_definition" not in saved.definition["supervisor"]
    assert "agent_definition_digest" not in saved.definition["supervisor"]
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


def test_unit_scoped_collaboration_grants_authorize_current_project_team_operations(service):
    context = scoped_context(
        unit_grant("collaboration.read"),
        unit_grant("collaboration.manage"),
        unit_grant("collaboration.run"),
        roles=("unit_admin",),
    )

    team = service.create(context, TeamCreateRequest(name="联合研判"))
    service.save_draft(context, team.id, TeamDraftUpdate(revision=1, draft=draft()))
    service.publish(context, team.id)
    service.set_enabled(context, team.id, True)

    assert service.get(context, team.id).id == team.id
    assert service.resolve_for_run(context, team.id).team_id == team.id


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
