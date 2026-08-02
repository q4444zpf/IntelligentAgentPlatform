from datetime import datetime, timezone
from typing import cast

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.conversations.models import Conversation, ToolInvocation
from app.conversations.repository import ConversationRepository


class RecordingSession:
    def __init__(self):
        self.statements = []

    def scalar(self, statement):
        self.statements.append(statement)
        if len(self.statements) == 1:
            return Conversation(
                id="c1", project_id="p1", owner_id="u1", title="洪水研判"
            )
        return 2


def test_message_sequence_locks_conversation_before_allocating():
    session = RecordingSession()
    repository = ConversationRepository(cast(Session, session))

    sequence = repository.next_message_sequence("c1")

    lock_sql = str(
        session.statements[0].compile(dialect=postgresql.dialect())
    )
    assert "FOR UPDATE" in lock_sql
    assert sequence == 3


def test_repository_lists_invocations_in_creation_order(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.conversations.models import AgentRun, Message
    from app.db.base import Base

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'repository.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with factory.begin() as session:
        conversation = Conversation(id="c1", project_id="p1", owner_id="u1", title="test")
        message = Message(id="m1", conversation_id="c1", sequence=1, role="user", content="test")
        run = AgentRun(id="r1", conversation_id="c1", trigger_message_id="m1", actor_type="agent", actor_id="a1", status="running")
        session.add_all([conversation, message, run])
        repository = ConversationRepository(session)
        repository.add_tool_invocation(ToolInvocation(run_id="r1", tool_call_id="b", tool_id="system.get_current_time", tool_version="1.0.0", status="started", arguments_summary={}, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)))
        repository.add_tool_invocation(ToolInvocation(run_id="r1", tool_call_id="a", tool_id="system.get_current_time", tool_version="1.0.0", status="started", arguments_summary={}, created_at=datetime(2026, 1, 2, tzinfo=timezone.utc)))
        assert [item.tool_call_id for item in repository.list_tool_invocations("r1")] == ["b", "a"]
