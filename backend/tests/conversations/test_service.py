from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.conversations.dispatcher import RunDispatcher
from app.conversations.repository import ConversationRepository
from app.conversations.schemas import ConversationCreate, MessageCreate
from app.conversations.service import ConversationNotFound, ConversationService
from app.core.request_context import RequestContext
from app.db.base import Base


class RecordingDispatcher(RunDispatcher):
    def __init__(self):
        self.run_ids: list[str] = []

    def dispatch(self, run_id: str) -> None:
        self.run_ids.append(run_id)


def build_service():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    dispatcher = RecordingDispatcher()
    return session, dispatcher, ConversationService(
        ConversationRepository(session), dispatcher
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
