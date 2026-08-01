from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.conversations.models import AgentRun, Conversation, Message, RunEvent
from app.conversations.repository import ConversationRepository
from app.db.base import Base
from app.runtime.harness import PlatformAgentHarness
from app.runtime.model_gateway import ModelResult, ModelUpstreamError


class SuccessfulGateway:
    def generate(self, messages: list[dict[str, str]]) -> ModelResult:
        assert messages == [{"role": "user", "content": "分析洪峰"}]
        return ModelResult(
            content="研判完成",
            prompt_tokens=11,
            completion_tokens=7,
            total_tokens=18,
        )


def build_queued_run(actor_type: str = "agent"):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
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
        actor_type=actor_type,
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
    return session, run.id


def test_completes_run_and_persists_assistant_message():
    session, run_id = build_queued_run()
    repository = ConversationRepository(session)

    PlatformAgentHarness(repository, SuccessfulGateway()).execute(run_id)

    run = session.get(AgentRun, run_id)
    messages = repository.get_run_messages(run_id)
    events = repository.list_events(run_id, 0)
    assert run is not None and run.status == "completed"
    assert messages[-1].role == "assistant"
    assert messages[-1].content == "研判完成"
    assert [event.event_type for event in events] == [
        "run.status",
        "run.status",
        "message.completed",
        "run.usage",
        "run.status",
    ]
    assert events[3].payload == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }
    session.close()
class FailingGateway:
    def generate(self, messages: list[dict[str, str]]) -> ModelResult:
        raise ModelUpstreamError("upstream rejected runtime-secret")


def test_fails_run_without_exposing_upstream_error_details():
    session, run_id = build_queued_run()
    repository = ConversationRepository(session)

    PlatformAgentHarness(repository, FailingGateway()).execute(run_id)

    run = session.get(AgentRun, run_id)
    events = repository.list_events(run_id, 0)
    assert run is not None and run.status == "failed"
    assert [event.event_type for event in events][-2:] == [
        "run.error",
        "run.status",
    ]
    assert events[-2].payload == {
        "code": "model_request_failed",
        "message": "模型调用失败，请检查默认模型配置或稍后重试",
    }
    assert "runtime-secret" not in str([event.payload for event in events])


def test_rejects_team_run_until_multi_agent_runtime_is_available():
    session, run_id = build_queued_run(actor_type="team")
    repository = ConversationRepository(session)

    PlatformAgentHarness(repository, SuccessfulGateway()).execute(run_id)

    run = session.get(AgentRun, run_id)
    events = repository.list_events(run_id, 0)
    assert run is not None and run.status == "failed"
    assert events[-2].payload["code"] == "unsupported_actor_type"
    assert all(event.event_type != "message.completed" for event in events)