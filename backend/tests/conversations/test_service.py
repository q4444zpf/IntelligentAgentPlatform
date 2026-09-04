from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.agents.service import BUILTIN_AGENT_ID, AgentNotFoundError
from app.audit.models import AuditEvent
from app.audit.recorder import AuditRecorder
from app.conversations.dispatcher import RunDispatcher
from app.conversations.models import AgentRun, Conversation, Message
from app.conversations.repository import ConversationRepository
from app.conversations.schemas import ConversationCreate, MessageCreate
from app.conversations.service import (
    AgentSelectionError,
    ConversationNotFound,
    ConversationService,
)
from app.core.request_context import RequestContext
from app.db.base import Base
from app.collaboration.schemas import TeamCreateRequest, TeamDraft, TeamDraftUpdate, TeamMemberDraft
from app.collaboration.service import TeamService
from app.identity.schemas import AuthorizationContext, PermissionGrant


class RecordingDispatcher(RunDispatcher):
    def __init__(self):
        self.run_ids: list[str] = []

    def dispatch(self, run_id: str) -> None:
        self.run_ids.append(run_id)


class StubAgentService:
    def __init__(self):
        self.agents = {
            BUILTIN_AGENT_ID: self._agent(BUILTIN_AGENT_ID),
            "flood": self._agent("flood"),
            "supervisor": self._agent("supervisor"),
            "member": self._agent("member"),
            "disabled-agent": self._agent("disabled-agent", enabled=False),
        }
        self.tool_service = SimpleNamespace(resolve_bindable=lambda tool_ids: [])
        self.tool_service.resolve_knowledge_sources = lambda tool_ids: []
        self.skill_service = SimpleNamespace()

    @staticmethod
    def _agent(agent_id: str, *, enabled: bool = True):
        return SimpleNamespace(
            id=agent_id,
            name=agent_id,
            description="",
            runtime_form="common",
            language="zh-CN",
            provider_id="provider-1",
            model="model-1",
            system_prompt="",
            context_prompt="",
            approval_policy="control_commands",
            skill_names=[],
            tool_ids=[],
            knowledge_source_ids=[],
            enabled=enabled,
            availability_scope="project",
            unit_id="unit-1",
            project_id="p1",
            allowed_project_ids=[],
        )

    def get_default(self):
        return self.agents[BUILTIN_AGENT_ID]

    def get_default_available(self, *, unit_id: str, project_id: str):
        return self.get_available(
            BUILTIN_AGENT_ID,
            unit_id=unit_id,
            project_id=project_id,
        )

    def get(self, agent_id: str):
        try:
            return self.agents[agent_id]
        except KeyError as error:
            raise AgentNotFoundError(agent_id) from error

    def get_available(self, agent_id: str, *, unit_id: str, project_id: str):
        agent = self.get(agent_id)
        if agent.unit_id != unit_id or agent.project_id != project_id:
            raise AgentNotFoundError(agent_id)
        return agent


class StubProviderService:
    def get(self, provider_id: str):
        if provider_id != "provider-1":
            raise KeyError(provider_id)
        return SimpleNamespace(
            id=provider_id,
            configured=True,
            enabled=True,
            models=[SimpleNamespace(id="model-1", enabled=True)],
        )


def build_service():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    dispatcher = RecordingDispatcher()
    return session, dispatcher, ConversationService(
        ConversationRepository(session),
        dispatcher,
        agent_service=StubAgentService(),
    )


def test_creates_message_run_and_initial_event_atomically():
    session, dispatcher, service = build_service()
    context = RequestContext(unit_id="unit-1", user_id="u1", project_id="p1")
    conversation = service.create_conversation(
        context, ConversationCreate(title="洪水研判")
    )
    accepted = service.create_message(
        context,
        conversation.id,
        MessageCreate(content="分析未来洪峰", actor_type="agent", actor_id="flood"),
    )
    assert accepted.run.status == "queued"
    assert service.list_events(
        context, accepted.run.id, after_sequence=0
    )[0].payload == {"status": "queued"}
    assert dispatcher.run_ids == [accepted.run.id]
    session.close()


def test_message_creation_records_agent_run_in_same_transaction():
    session, _, service = build_service()
    context = RequestContext(
        unit_id="unit-1",
        user_id="u1",
        project_id="p1",
        roles=frozenset({"user", "project_admin"}),
    )
    conversation = service.create_conversation(context, ConversationCreate(title="x"))
    accepted = service.create_message(context, conversation.id, MessageCreate(content="x", actor_type="agent", actor_id="flood"))
    event = session.scalar(select(AuditEvent))
    assert event.idempotency_key == f"agent:{accepted.run.id}:created"
    assert (event.action, event.unit_id, event.project_id, event.user_id) == ("agent.run.created", "unit-1", "p1", "u1")
    assert session.get(AgentRun, accepted.run.id).actor_roles_json == [
        "project_admin",
        "user",
    ]
    assert event.actor_roles_json == ["project_admin", "user"]
    assert event.authorization_scope == "project"
    assert event.event_scope == "project"
    assert event.resource_type == "agent"
    assert event.metadata_json == {}


def test_audit_failure_rolls_back_message_and_run():
    class FailingRecorder(AuditRecorder):
        def record(self, session, request):
            raise RuntimeError("audit unavailable")

    session, dispatcher, service = build_service()
    service.audit_recorder = FailingRecorder()
    context = RequestContext(unit_id="unit-1", user_id="u1", project_id="p1")
    conversation = service.create_conversation(context, ConversationCreate(title="x"))
    with pytest.raises(RuntimeError, match="audit unavailable"):
        service.create_message(context, conversation.id, MessageCreate(content="x", actor_type="agent", actor_id="flood"))
    assert session.scalar(select(func.count()).select_from(Message)) == 0
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 0
    assert dispatcher.run_ids == []


def test_message_activity_advances_conversation_recency():
    session, _, service = build_service()
    context = RequestContext(unit_id="unit-1", user_id="u1", project_id="p1")
    conversation = service.create_conversation(
        context, ConversationCreate(title="洪水研判")
    )
    stored = session.get(Conversation, conversation.id)
    assert stored is not None
    stored.updated_at = datetime(2020, 1, 1)
    session.commit()

    service.create_message(
        context,
        conversation.id,
        MessageCreate(content="更新研判", actor_type="agent", actor_id="flood"),
    )

    session.refresh(stored)
    assert stored.updated_at > datetime(2020, 1, 1)
    session.close()


def test_cannot_read_another_project_conversation():
    _, _, service = build_service()
    owner = RequestContext(unit_id="unit-1", user_id="u1", project_id="p1")
    conversation = service.create_conversation(
        owner, ConversationCreate(title="项目一")
    )
    other = RequestContext(unit_id="unit-1", user_id="u2", project_id="p2")
    try:
        service.get_conversation(other, conversation.id)
    except ConversationNotFound:
        pass
    else:
        raise AssertionError("cross-project access must look like not found")


def test_cannot_read_another_users_private_conversation_in_same_project():
    _, _, service = build_service()
    owner = RequestContext(unit_id="unit-1", user_id="u1", project_id="p1")
    conversation = service.create_conversation(
        owner, ConversationCreate(title="个人研判")
    )
    other_user = RequestContext(unit_id="unit-1", user_id="u2", project_id="p1")
    try:
        service.get_conversation(other_user, conversation.id)
    except ConversationNotFound:
        pass
    else:
        raise AssertionError("cross-owner access must look like not found")


def test_uses_default_agent_when_agent_id_is_omitted():
    session, dispatcher, service = build_service()
    context = RequestContext(unit_id="unit-1", user_id="u1", project_id="p1")
    conversation = service.create_conversation(
        context, ConversationCreate(title="默认智能体")
    )

    accepted = service.create_message(
        context,
        conversation.id,
        MessageCreate(content="分析当前水情", actor_type="agent"),
    )

    assert accepted.run.actor_id == BUILTIN_AGENT_ID
    assert dispatcher.run_ids == [accepted.run.id]
    session.close()


def test_preserves_explicit_enabled_agent():
    session, _, service = build_service()
    context = RequestContext(unit_id="unit-1", user_id="u1", project_id="p1")
    conversation = service.create_conversation(
        context, ConversationCreate(title="指定智能体")
    )
    accepted = service.create_message(
        context,
        conversation.id,
        MessageCreate(content="分析洪峰", actor_type="agent", actor_id="flood"),
    )

    assert accepted.run.actor_id == "flood"
    session.close()


def test_message_contract_accepts_team_uuid_actor_id():
    request = MessageCreate(
        content="联合研判",
        actor_type="team",
        actor_id="8c3c8a65-709b-4187-aec8-4a9341f817de",
    )

    assert request.actor_id == "8c3c8a65-709b-4187-aec8-4a9341f817de"


@pytest.mark.parametrize("actor_id", ["missing-agent", "disabled-agent"])
def test_rejects_unavailable_explicit_agent_without_persisting(actor_id):
    session, dispatcher, service = build_service()
    context = RequestContext(unit_id="unit-1", user_id="u1", project_id="p1")
    conversation = service.create_conversation(
        context, ConversationCreate(title="无效智能体")
    )

    with pytest.raises(AgentSelectionError):
        service.create_message(
            context,
            conversation.id,
            MessageCreate(
                content="分析洪峰", actor_type="agent", actor_id=actor_id
            ),
        )

    assert session.scalar(select(func.count()).select_from(Message)) == 0
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 0
    assert dispatcher.run_ids == []
    session.close()


def test_requires_actor_id_for_team_without_persisting():
    session, dispatcher, service = build_service()
    context = RequestContext(unit_id="unit-1", user_id="u1", project_id="p1")
    conversation = service.create_conversation(
        context, ConversationCreate(title="团队协作")
    )

    with pytest.raises(AgentSelectionError):
        service.create_message(
            context,
            conversation.id,
            MessageCreate(content="联合研判", actor_type="team"),
        )

    assert session.scalar(select(func.count()).select_from(Message)) == 0
    assert session.scalar(select(func.count()).select_from(AgentRun)) == 0
    assert dispatcher.run_ids == []
    session.close()


def test_team_message_acceptance_records_selected_version_and_run_audit():
    session, dispatcher, _ = build_service()
    manager_context = _team_context("collaboration.manage", "collaboration.run")
    team_service = TeamService(
        session,
        agent_service=StubAgentService(),
        provider_service=StubProviderService(),
    )
    team = team_service.create(manager_context, TeamCreateRequest(name="联合研判"))
    team_service.save_draft(
        manager_context,
        team.id,
        TeamDraftUpdate(revision=1, draft=_team_draft()),
    )
    published = team_service.publish(manager_context, team.id)
    team_service.set_enabled(manager_context, team.id, True)
    service = ConversationService(
        ConversationRepository(session),
        dispatcher,
        agent_service=StubAgentService(),
        team_service=team_service,
    )
    conversation = service.create_conversation(manager_context, ConversationCreate(title="团队协作"))

    accepted = service.create_message(
        manager_context,
        conversation.id,
        MessageCreate(content="联合研判", actor_type="team", actor_id=team.id),
    )
    event = session.scalar(select(AuditEvent).where(AuditEvent.run_id == accepted.run.id))

    assert event is not None
    assert (event.action, event.resource_type, event.resource_id) == (
        "team.run.created",
        "team",
        team.id,
    )
    assert event.run_id == accepted.run.id
    assert event.project_id == "p1"
    assert event.actor_roles_json == ["custom_operator"]
    assert event.metadata_json == {"actor_version_id": published.id}


def _team_context(*permissions: str) -> RequestContext:
    authorization = AuthorizationContext(
        session_id="test-session",
        user_id="u1",
        unit_id="unit-1",
        current_project_id="p1",
        auth_method="dev_test",
        authorization_version=1,
        role_codes=("custom_operator",),
        grants=tuple(
            PermissionGrant(permission, "project", frozenset({"p1"}), None)
            for permission in permissions
        ),
    )
    return RequestContext(
        unit_id="unit-1",
        user_id="u1",
        project_id="p1",
        authorization_context=authorization,
    )


def _team_draft() -> TeamDraft:
    return TeamDraft(
        supervisor=TeamMemberDraft(
            agent_id="supervisor",
            responsibility="coordinate",
        ),
        members=[
            TeamMemberDraft(
                agent_id="member",
                responsibility="review",
            )
        ],
        max_steps=4,
        max_parallel_members=1,
        timeout_seconds=60,
    )
