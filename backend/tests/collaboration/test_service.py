from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.schemas import AgentInfo
from app.agents.service import AgentNotFoundError
from app.audit.models import AuditEvent
from app.collaboration.schemas import TeamCreateRequest, TeamDraft, TeamDraftUpdate, TeamMemberDraft, TeamMetadataUpdate
from app.collaboration.repository import TeamDefinitionValidationError, TeamNotFoundError
from app.collaboration.service import TeamPermissionError, TeamService, TeamUnavailableError
from app.core.request_context import RequestContext
from app.db.base import Base
from app.identity.schemas import AuthorizationContext, PermissionGrant
from app.model_providers.schemas import ModelInfo, ProviderInfo
from app.skills.service import SkillNotFoundError
from app.tools.service import ToolNotFoundError, ToolValidationError


def agent(
    agent_id: str,
    *,
    enabled: bool = True,
    tool_ids=None,
    skill_names=None,
    knowledge_source_ids=None,
    availability_scope="project",
    unit_id="unit-1",
    project_id="p1",
    allowed_project_ids=None,
):
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
        knowledge_source_ids=knowledge_source_ids or [],
        enabled=enabled,
        availability_scope=availability_scope,
        unit_id=unit_id,
        project_id=project_id,
        allowed_project_ids=allowed_project_ids or [],
        pinned=False,
        is_builtin=False,
        is_default=False,
        startup_status="ready" if enabled else "disabled",
        workspace_dir=f"/workspace/{agent_id}",
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        updated_at=datetime(2026, 9, 1, tzinfo=UTC),
    )


def tool(tool_id: str, *, enabled: bool = True, source="builtin"):
    return SimpleNamespace(
        tool_id=tool_id,
        version="1",
        name=tool_id,
        description=f"{tool_id} description",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        source=source,
        risk_level="low",
        source_resource_id=(tool_id if source == "knowledge" else None),
        source_capability_id=None,
        requires_approval=False,
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

    def resolve_knowledge_sources(self, tool_ids):
        resolved = self.resolve_bindable(tool_ids)
        for item in resolved:
            if item.source != "knowledge":
                raise ToolValidationError(
                    f"Tool '{item.tool_id}' is not a knowledge source"
                )
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
            skills
            or [
                SimpleNamespace(
                    name="forecast",
                    description="Forecast water levels",
                    version="1",
                    content="---\nname: forecast\ndescription: Forecast water levels\nversion: '1'\n---\n",
                    source="created",
                    enabled=True,
                    tags=["hydrology"],
                    metadata={"owner": "platform"},
                    file_count=1,
                    updated_at=datetime(2026, 9, 1, tzinfo=UTC),
                )
            ]
        )

    def get(self, agent_id):
        try:
            return self.agents[agent_id]
        except KeyError as error:
            raise AgentNotFoundError(agent_id) from error


class StaticProviderService:
    def __init__(self, *, configured=True, provider_enabled=True, model_enabled=True):
        self.provider = ProviderInfo(
            id="provider-1",
            name="Provider",
            base_url="https://models.example.test/v1",
            configured=configured,
            enabled=provider_enabled,
            models=[
                ModelInfo(
                    id="supervisor-model",
                    name="Supervisor",
                    enabled=model_enabled,
                ),
                ModelInfo(
                    id="member-model",
                    name="Member",
                    enabled=model_enabled,
                ),
            ],
        )

    def get(self, provider_id):
        if provider_id != self.provider.id:
            raise KeyError(provider_id)
        return self.provider


@pytest.fixture
def service():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield TeamService(
            session,
            agent_service=StaticAgentService(),
            provider_service=StaticProviderService(),
        )


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
        "allowed_project_ids": [],
        "approval_policy": "control_commands",
        "availability_scope": "project",
        "context_prompt": "supervisor context prompt",
        "description": "supervisor description",
        "enabled": True,
        "id": "supervisor",
        "language": "zh-CN",
        "model": "supervisor-model",
        "name": "supervisor name",
        "knowledge_source_ids": [],
        "knowledge_sources": [],
        "provider_id": "provider-1",
        "project_id": "p1",
        "runtime_form": "common",
        "skill_names": ["forecast"],
        "skills": [
            {
                "content": "---\nname: forecast\ndescription: Forecast water levels\nversion: '1'\n---\n",
                "description": "Forecast water levels",
                "enabled": True,
                "file_count": 1,
                "metadata": {"owner": "platform"},
                "name": "forecast",
                "source": "created",
                "tags": ["hydrology"],
                "updated_at": "2026-09-01T00:00:00Z",
                "version": "1",
            }
        ],
        "system_prompt": "supervisor system prompt",
        "tool_ids": ["forecast.read"],
        "tools": [
            {
                "description": "forecast.read description",
                "enabled": True,
                "input_schema": {"type": "object"},
                "name": "forecast.read",
                "output_schema": {"type": "object"},
                "published": True,
                "requires_approval": False,
                "risk_level": "low",
                "source": "builtin",
                "source_capability_id": None,
                "source_resource_id": None,
                "source_available": True,
                "tool_id": "forecast.read",
                "version": "1",
            }
        ],
        "unit_id": "unit-1",
    }


@pytest.mark.parametrize("unavailable", ["missing", "disabled", "cross_project"])
def test_publish_rejects_unavailable_agents(unavailable):
    agent_service = StaticAgentService()
    if unavailable == "missing":
        del agent_service.agents["member"]
    elif unavailable == "disabled":
        agent_service.agents["member"] = agent("member", enabled=False)
    else:
        agent_service.agents["member"] = agent(
            "member", unit_id="unit-1", project_id="other-project"
        )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        service = TeamService(
            session,
            agent_service=agent_service,
            provider_service=StaticProviderService(),
        )
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
        service = TeamService(
            session,
            agent_service=agent_service,
            provider_service=StaticProviderService(),
        )
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
        service = TeamService(
            session,
            agent_service=agent_service,
            provider_service=StaticProviderService(),
        )
        context = admin()
        team = service.create(context, TeamCreateRequest(name="联合研判"))
        service.save_draft(
            context, team.id, TeamDraftUpdate(revision=1, draft=draft())
        )

        with pytest.raises(TeamDefinitionValidationError, match="disabled.tool"):
            service.publish(context, team.id)


@pytest.mark.parametrize(
    ("availability_scope", "unit_id", "project_id", "allowed_project_ids"),
    [
        ("project", "unit-1", "other-project", []),
        ("common", None, None, ["other-project"]),
        ("common", None, None, []),
    ],
)
def test_publish_rejects_agents_not_explicitly_available_to_project(
    availability_scope, unit_id, project_id, allowed_project_ids
):
    agent_service = StaticAgentService()
    agent_service.agents["member"] = agent(
        "member",
        availability_scope=availability_scope,
        unit_id=unit_id,
        project_id=project_id,
        allowed_project_ids=allowed_project_ids,
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        team_service = TeamService(
            session,
            agent_service=agent_service,
            provider_service=StaticProviderService(),
        )
        context = admin()
        team = team_service.create(
            context, TeamCreateRequest(name="Unavailable Agent")
        )
        team_service.save_draft(
            context, team.id, TeamDraftUpdate(revision=1, draft=draft())
        )

        with pytest.raises(TeamDefinitionValidationError, match="Agent 'member'"):
            team_service.publish(context, team.id)


@pytest.mark.parametrize(
    ("provider_kwargs", "expected"),
    [
        ({"configured": False}, "Provider"),
        ({"provider_enabled": False}, "Provider"),
        ({"model_enabled": False}, "model"),
    ],
)
def test_publish_rejects_unavailable_provider_or_model(provider_kwargs, expected):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        team_service = TeamService(
            session,
            agent_service=StaticAgentService(),
            provider_service=StaticProviderService(**provider_kwargs),
        )
        context = admin()
        team = team_service.create(context, TeamCreateRequest(name="Invalid model"))
        team_service.save_draft(
            context, team.id, TeamDraftUpdate(revision=1, draft=draft())
        )

        with pytest.raises(TeamDefinitionValidationError, match=expected):
            team_service.publish(context, team.id)


def test_publish_captures_and_validates_full_knowledge_source_definition():
    knowledge_id = "knowledge.reservoir.manual"
    agent_service = StaticAgentService(
        agents={
            "supervisor": agent(
                "supervisor", knowledge_source_ids=[knowledge_id]
            ),
            "member": agent("member"),
        },
        tools=[tool(knowledge_id, source="knowledge")],
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        team_service = TeamService(
            session,
            agent_service=agent_service,
            provider_service=StaticProviderService(),
        )
        context = admin()
        team = team_service.create(context, TeamCreateRequest(name="Knowledge"))
        requested = draft().model_copy(
            update={
                "knowledge_source_ids": [knowledge_id],
                "supervisor": draft().supervisor.model_copy(
                    update={"knowledge_source_ids": [knowledge_id]}
                ),
            }
        )
        team_service.save_draft(
            context,
            team.id,
            TeamDraftUpdate(revision=1, draft=requested),
        )

        published = team_service.publish(context, team.id)
        stored = team_service.repository.get_version_by_id(published.id)
        supervisor = next(item for item in stored.members if item.role == "supervisor")
        assert supervisor.agent_definition["knowledge_sources"] == [
            {
                "description": f"{knowledge_id} description",
                "enabled": True,
                "input_schema": {"type": "object"},
                "name": knowledge_id,
                "output_schema": {"type": "object"},
                "published": True,
                "requires_approval": False,
                "risk_level": "low",
                "source": "knowledge",
                "source_available": True,
                "source_capability_id": None,
                "source_resource_id": knowledge_id,
                "tool_id": knowledge_id,
                "version": "1",
            }
        ]


@pytest.mark.parametrize(
    ("max_steps", "max_parallel_members", "max_subagents", "max_members", "message"),
    [
        (5, 1, 4, 8, "max_steps"),
        (4, 2, 1, 8, "max_parallel_members"),
        (4, 1, 4, 1, "member count"),
    ],
)
def test_publish_enforces_configured_runner_ceilings(
    max_steps, max_parallel_members, max_subagents, max_members, message
):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        team_service = TeamService(
            session,
            agent_service=StaticAgentService(),
            provider_service=StaticProviderService(),
            max_team_steps=4,
            max_parallel_members=1,
            max_subagents=max_subagents,
            max_team_members=max_members,
        )
        context = admin()
        team = team_service.create(context, TeamCreateRequest(name="Limits"))
        requested = draft().model_copy(
            update={
                "max_steps": max_steps,
                "max_parallel_members": max_parallel_members,
            }
        )
        team_service.save_draft(
            context, team.id, TeamDraftUpdate(revision=1, draft=requested)
        )

        with pytest.raises(TeamDefinitionValidationError, match=message):
            team_service.publish(context, team.id)


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


def test_publish_refreshes_stale_session_and_captures_latest_draft(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'teams.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    first_session = factory()
    second_session = factory()
    try:
        first = TeamService(
            first_session,
            agent_service=StaticAgentService(),
            provider_service=StaticProviderService(),
        )
        second = TeamService(
            second_session,
            agent_service=StaticAgentService(),
            provider_service=StaticProviderService(),
        )
        context = admin()
        team = first.create(context, TeamCreateRequest(name="联合研判"))
        first.save_draft(
            context,
            team.id,
            TeamDraftUpdate(revision=1, draft=draft()),
        )

        stale_team = first.repository.get_scoped("unit-1", "p1", team.id)
        stale_draft = first.repository.get_version(team.id, 0)
        assert stale_team.draft_revision == 2
        assert stale_draft.max_steps == 4

        second.save_draft(
            context,
            team.id,
            TeamDraftUpdate(
                revision=2,
                draft=draft().model_copy(update={"max_steps": 7}),
            ),
        )

        published = first.publish(context, team.id)

        assert published.max_steps == 7
        assert first.repository.get_version_by_id(published.id).definition[
            "max_steps"
        ] == 7
    finally:
        first_session.close()
        second_session.close()
        engine.dispose()


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
