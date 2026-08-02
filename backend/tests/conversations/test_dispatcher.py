from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.conversations.dispatcher import ThreadRunDispatcher
from app.conversations.models import AgentRun, Conversation, Message, RunEvent, ToolInvocation
from app.db.base import Base
from app.db.platform_models import RegisteredToolRecord
from app.runtime.model_gateway import ModelResult, ModelSelection
from app.tools.schemas import ToolCall


class RecordingAgentService:
    def get(self, agent_id: str):
        assert agent_id == "flood"
        return SimpleNamespace(
            enabled=True,
            system_prompt="",
            context_prompt="",
            provider_id="deepseek",
            model="deepseek-chat",
            tool_ids=[],
        )


class RecordingGateway:
    def __init__(self):
        self.calls = []

    def generate(
        self,
        messages: list[dict[str, str]],
        selection: ModelSelection | None = None,
        tools=None,
    ) -> ModelResult:
        self.calls.append((messages, selection))
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
            agent_service_factory=RecordingAgentService,
            max_workers=1,
        )
        dispatcher.dispatch(run_id)
        dispatcher.shutdown()

        assert request_session.get(AgentRun, run_id).status == "queued"

    with factory() as verification_session:
        completed = verification_session.get(AgentRun, run_id)
        assert completed is not None and completed.status == "completed"
    assert gateway.calls == [(
        [{"role": "user", "content": "分析洪峰"}],
        ModelSelection("deepseek", "deepseek-chat"),
    )]


class ToolBoundAgentService:
    def get(self, agent_id: str):
        assert agent_id == "flood"
        return SimpleNamespace(
            enabled=True,
            system_prompt="",
            context_prompt="",
            provider_id="deepseek",
            model="deepseek-chat",
            tool_ids=["system.get_current_time"],
        )


class TwoRoundToolModel:
    def __init__(self):
        self.calls = []

    def generate(self, messages, selection=None, tools=None):
        self.calls.append((list(messages), list(tools or [])))
        if len(self.calls) == 1:
            return ModelResult(
                None,
                tool_calls=(ToolCall("dispatcher-time-1", "system.get_current_time", {}),),
            )
        return ModelResult("后台时间查询完成")


def test_dispatcher_executes_tool_loop_entirely_in_supplied_database(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'dispatcher-tools.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    model = TwoRoundToolModel()
    with factory.begin() as session:
        conversation = Conversation(project_id="p-tool", owner_id="u-tool", title="时间")
        session.add(conversation)
        session.flush()
        message = Message(conversation_id=conversation.id, role="user", content="现在几点？")
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
        session.add(RunEvent(run_id=run.id, sequence=1, event_type="run.status", payload={"status": "queued"}))
        run_id = run.id
        conversation_id = conversation.id

    dispatcher = ThreadRunDispatcher(
        session_factory=factory,
        gateway_factory=lambda: model,
        agent_service_factory=ToolBoundAgentService,
        max_workers=1,
    )
    dispatcher.dispatch(run_id)
    dispatcher.shutdown()

    with factory() as session:
        assert session.get(Conversation, conversation_id) is not None
        assert session.get(AgentRun, run_id).status == "completed"
        assert session.get(RegisteredToolRecord, "system.get_current_time") is not None
        invocation = session.query(ToolInvocation).filter_by(run_id=run_id).one()
        assert invocation.tool_call_id == "dispatcher-time-1"
        assert invocation.status == "completed"
        messages = session.query(Message).filter_by(conversation_id=conversation_id).order_by(Message.sequence).all()
        assert messages[-1].content == "后台时间查询完成"
    assert len(model.calls) == 2
    assert model.calls[0][1][0].tool_id == "system.get_current_time"
    assert model.calls[1][0][-1]["role"] == "tool"