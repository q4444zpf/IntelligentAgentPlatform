from typing import cast

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.conversations.models import Conversation
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
