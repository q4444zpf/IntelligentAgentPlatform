from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.conversations.dispatcher import ThreadRunDispatcher
from app.conversations.models import AgentRun, Conversation, Message, RunEvent
from app.db.base import Base
from app.runtime.model_gateway import ModelResult


class RecordingGateway:
    def __init__(self):
        self.calls: list[list[dict[str, str]]] = []

    def generate(self, messages: list[dict[str, str]]) -> ModelResult:
        self.calls.append(messages)
        return ModelResult(content="后台研判完成")


def test_dispatches_run_with_an_independent_database_session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'runtime.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    gateway = RecordingGateway()

    with factory() as request_session:
        conversation = Conversation(
            project_id="p1", owner_id="u1", title="洪水研判"
        )
        request_session.add(conversation)
        request_session.flush()
        message = Message(
            conversation_id=conversation.id,
            role="user",
            content="分析洪峰",
        )
        request_session.add(message)
        request_session.flush()
        run = AgentRun(
            conversation_id=conversation.id,
            trigger_message_id=message.id,
            actor_type="agent",
            actor_id="flood",
            status="queued",
        )
        request_session.add(run)
        request_session.flush()
        request_session.add(
            RunEvent(
                run_id=run.id,
                sequence=1,
                event_type="run.status",
                payload={"status": "queued"},
            )
        )
        request_session.commit()
        run_id = run.id

        dispatcher = ThreadRunDispatcher(
            session_factory=factory,
            gateway_factory=lambda: gateway,
            max_workers=1,
        )
        dispatcher.dispatch(run_id)
        dispatcher.shutdown()

        assert request_session.get(AgentRun, run_id).status == "queued"

    with factory() as verification_session:
        completed = verification_session.get(AgentRun, run_id)
        assert completed is not None and completed.status == "completed"
    assert gateway.calls == [[{"role": "user", "content": "分析洪峰"}]]
