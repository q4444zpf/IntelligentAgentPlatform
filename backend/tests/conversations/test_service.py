from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.agents.service import BUILTIN_AGENT_ID, AgentNotFoundError
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


class RecordingDispatcher(RunDispatcher):
    def __init__(self):
        self.run_ids: list[str] = []

    def dispatch(self, run_id: str) -> None:
        self.run_ids.append(run_id)


class StubAgentService:
    def __init__(self):
        self.agents = {
            BUILTIN_AGENT_ID: SimpleNamespace(id=BUILTIN_AGENT_ID, enabled=True),
            "flood": SimpleNamespace(id="flood", enabled=True),
            "disabled-agent": SimpleNamespace(id="disabled-agent", enabled=False),
        }

    def get_default(self):
        return self.agents[BUILTIN_AGENT_ID]

    def get(self, agent_id: str):
        try:
            return self.agents[agent_id]
        except KeyError as error:
            raise AgentNotFoundError(agent_id) from error


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
    context = RequestContext(user_id="u1", project_id="p1")
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


def test_message_activity_advances_conversation_recency():
    session, _, service = build_service()
    context = RequestContext(user_id="u1", project_id="p1")
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
    owner = RequestContext(user_id="u1", project_id="p1")
    conversation = service.create_conversation(
        owner, ConversationCreate(title="项目一")
    )
    other = RequestContext(user_id="u2", project_id="p2")
    try:
        service.get_conversation(other, conversation.id)
    except ConversationNotFound:
        pass
    else:
        raise AssertionError("cross-project access must look like not found")


def test_cannot_read_another_users_private_conversation_in_same_project():
    _, _, service = build_service()
    owner = RequestContext(user_id="u1", project_id="p1")
    conversation = service.create_conversation(
        owner, ConversationCreate(title="个人研判")
    )
    other_user = RequestContext(user_id="u2", project_id="p1")
    try:
        service.get_conversation(other_user, conversation.id)
    except ConversationNotFound:
        pass
    else:
        raise AssertionError("cross-owner access must look like not found")


def test_uses_default_agent_when_agent_id_is_omitted():
    session, dispatcher, service = build_service()
    context = RequestContext(user_id="u1", project_id="p1")
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
    context = RequestContext(user_id="u1", project_id="p1")
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


@pytest.mark.parametrize("actor_id", ["missing-agent", "disabled-agent"])
def test_rejects_unavailable_explicit_agent_without_persisting(actor_id):
    session, dispatcher, service = build_service()
    context = RequestContext(user_id="u1", project_id="p1")
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
    context = RequestContext(user_id="u1", project_id="p1")
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