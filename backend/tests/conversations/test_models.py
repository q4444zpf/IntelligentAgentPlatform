from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.conversations.models import AgentRun, Conversation, Message, RunEvent
from app.db.base import Base


def test_persists_project_scoped_conversation_graph():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        conversation = Conversation(project_id="p1", owner_id="u1", title="洪水研判")
        session.add(conversation)
        session.flush()
        message = Message(
            conversation_id=conversation.id,
            role="user",
            content="分析洪峰",
        )
        session.add(message)
        session.flush()
        run = AgentRun(
            conversation_id=conversation.id,
            trigger_message_id=message.id,
            actor_type="agent",
            actor_id="flood",
            status="queued",
        )
        session.add(run)
        session.flush()
        session.add(
            RunEvent(
                run_id=run.id,
                sequence=1,
                event_type="run.status",
                payload={"status": "queued"},
            )
        )
        session.commit()
        assert session.scalar(select(Conversation)).project_id == "p1"
        assert session.scalar(select(RunEvent)).sequence == 1
