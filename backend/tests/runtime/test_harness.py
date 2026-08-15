from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.service import AgentNotFoundError
from app.audit.recorder import AuditRecorder
from app.audit.models import AuditEvent
from app.conversations.models import AgentRun, Conversation, Message, RunEvent, ToolInvocation
from app.conversations.repository import ConversationRepository
from app.db.base import Base
from app.runtime.harness import MAX_MODEL_ITERATIONS, MAX_TOOL_CALLS, PlatformAgentHarness
from app.runtime.model_gateway import (
    ModelResult,
    ModelSelection,
    ModelUpstreamError,
)
from app.tools.gateway import ToolGateway
from app.tools.schemas import ToolCall, ToolDefinition, ToolExecutionResult, ToolRuntimeError
from app.tools.service import ToolService
from app.tools.store import ToolStore
from app.artifacts.models import ArtifactRecord


class FakeAgentService:
    def __init__(
        self,
        *,
        enabled=True,
        system_prompt="你是洪水研判智能体",
        context_prompt="结合当前流域上下文",
        provider_id="deepseek",
        model="deepseek-chat",
        missing=False,
        tool_ids=None,
    ):
        self.agent = SimpleNamespace(
            enabled=enabled,
            system_prompt=system_prompt,
            context_prompt=context_prompt,
            provider_id=provider_id,
            model=model,
            tool_ids=tool_ids or [],
        )
        self.missing = missing

    def get(self, agent_id: str):
        assert agent_id == "flood"
        if self.missing:
            raise AgentNotFoundError(agent_id)
        return self.agent


class SuccessfulGateway:
    def generate(
        self,
        messages: list[dict[str, str]],
        selection: ModelSelection | None = None,
        tools=None,
    ) -> ModelResult:
        assert messages == [
            {"role": "system", "content": "你是洪水研判智能体"},
            {"role": "system", "content": "结合当前流域上下文"},
            {"role": "user", "content": "分析洪峰"},
        ]
        assert selection == ModelSelection("deepseek", "deepseek-chat")
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
    conversation = Conversation(
        unit_id="unit-1", project_id="p1", owner_id="u1", title="洪水研判"
    )
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
        actor_roles_json=["project_admin", "user"],
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

    PlatformAgentHarness(
        repository, SuccessfulGateway(), FakeAgentService()
    ).execute(run_id)

    run = session.get(AgentRun, run_id)
    assert run is not None
    messages = repository.list_messages(
        "unit-1", "p1", "u1", run.conversation_id
    )
    events = repository.list_events(run_id, 0)
    assert run.status == "completed"
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


def test_completed_run_persists_result_artifact_when_storage_is_configured():
    class FakeStorage:
        def __init__(self):
            self.objects = {}

        def put_bytes(self, object_key, data, content_type):
            self.objects[object_key] = (data, content_type)

        def presigned_get_url(self, object_key, expires_seconds=900):
            return object_key

    session, run_id = build_queued_run()
    storage = FakeStorage()
    PlatformAgentHarness(
        ConversationRepository(session), SuccessfulGateway(), FakeAgentService(),
        artifact_storage=storage,
    ).execute(run_id)

    artifact = session.query(ArtifactRecord).filter_by(run_id=run_id).one()
    assert artifact.filename == "run-result.txt"
    assert artifact.content_type == "text/plain; charset=utf-8"
    assert storage.objects[artifact.object_key][0] == "研判完成".encode("utf-8")
    session.close()


def test_artifact_storage_outage_does_not_lose_completed_model_result():
    class FailingStorage:
        def put_bytes(self, object_key, data, content_type):
            raise RuntimeError("minio password=/internal/path")

        def delete_object(self, object_key):
            raise AssertionError("no completed upload should be deleted")

    session, run_id = build_queued_run()
    repository = ConversationRepository(session)

    PlatformAgentHarness(
        repository,
        SuccessfulGateway(),
        FakeAgentService(),
        artifact_storage=FailingStorage(),
    ).execute(run_id)

    assert repository.get_run_by_id(run_id).status == "completed"
    assert repository.list_messages(
        "unit-1", "p1", "u1", repository.get_run_by_id(run_id).conversation_id
    )[-1].content == "研判完成"
    failure = next(
        event
        for event in repository.list_events(run_id, 0)
        if event.event_type == "artifact.persistence_failed"
    )
    assert failure.payload == {
        "code": "artifact_persistence_failed",
        "message": "成果文件保存失败",
    }
    session.close()


def test_artifact_upload_is_deleted_when_database_commit_fails():
    from sqlalchemy import event

    class FakeStorage:
        def __init__(self):
            self.objects = {}
            self.deleted = []

        def put_bytes(self, object_key, data, content_type):
            self.objects[object_key] = data

        def delete_object(self, object_key):
            self.deleted.append(object_key)
            self.objects.pop(object_key, None)

    session, run_id = build_queued_run()
    repository = ConversationRepository(session)
    storage = FakeStorage()
    failed = False

    def fail_artifact_commit(current_session):
        nonlocal failed
        if not failed and any(isinstance(row, ArtifactRecord) for row in current_session.new):
            failed = True
            raise RuntimeError("database unavailable")

    event.listen(session, "before_commit", fail_artifact_commit)
    PlatformAgentHarness(
        repository,
        SuccessfulGateway(),
        FakeAgentService(),
        artifact_storage=storage,
    ).execute(run_id)
    event.remove(session, "before_commit", fail_artifact_commit)

    assert repository.get_run_by_id(run_id).status == "completed"
    assert storage.objects == {}
    assert len(storage.deleted) == 1
    assert session.query(ArtifactRecord).filter_by(run_id=run_id).count() == 0
    assert any(
        event_row.event_type == "artifact.persistence_failed"
        for event_row in repository.list_events(run_id, 0)
    )
    session.close()


def test_build_messages_explicitly_disallows_unavailable_tool_claims():
    session, run_id = build_queued_run()
    repository = ConversationRepository(session)
    harness = PlatformAgentHarness(repository, SuccessfulGateway(), FakeAgentService(tool_ids=[]), tool_service=object())

    messages = harness._build_messages(run_id, FakeAgentService(tool_ids=[]).agent)

    assert any(
        message["role"] == "system"
        and "不可调用任何工具" in message["content"]
        and "不得声称已经调用工具" in message["content"]
        for message in messages
    )
    assert messages[-1]["content"].startswith("当前智能体未授权任何工具")
    session.close()


def test_build_messages_reconstructs_completed_approved_tool_call_for_resume():
    session, run_id = build_queued_run()
    repository = ConversationRepository(session)
    run = repository.get_run_by_id(run_id)
    session.add(ToolInvocation(run_id=run_id, tool_call_id="c1", tool_id="system.one", tool_version="1", status="completed", arguments_summary={"amount": 10}, result_summary={"ok": True}))
    session.commit()
    harness = PlatformAgentHarness(repository, SuccessfulGateway(), FakeAgentService())
    messages = harness._build_messages(run_id, FakeAgentService().agent)
    assert messages[-2]["role"] == "assistant"
    assert messages[-2]["tool_calls"][0]["id"] == "c1"
    assert messages[-1] == {"role": "tool", "tool_call_id": "c1", "content": '{"ok":true}'}
    session.close()


def test_records_agent_status_and_safe_llm_iteration_events_idempotently():
    session, run_id = build_queued_run()
    repository = ConversationRepository(session)
    harness = PlatformAgentHarness(repository, SuccessfulGateway(), FakeAgentService())
    harness.execute(run_id)
    harness._complete(run_id, "replayed", {}, {})
    events = list(session.scalars(select(AuditEvent)))
    assert {event.action for event in events} == {"agent.run.running", "llm.invoke.succeeded", "agent.run.completed"}
    llm = next(event for event in events if event.source == "llm")
    assert llm.idempotency_key == f"llm:{run_id}:0:succeeded"
    assert llm.metadata_json == {"provider": "deepseek", "model": "deepseek-chat", "iteration": 0, "prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    assert "分析洪峰" not in str(events)


def test_records_failed_llm_and_agent_without_raw_error():
    session, run_id = build_queued_run()
    PlatformAgentHarness(ConversationRepository(session), FailingGateway(), FakeAgentService()).execute(run_id)
    events = list(session.scalars(select(AuditEvent)))
    assert {event.action for event in events} == {"agent.run.running", "llm.invoke.failed", "agent.run.failed"}
    assert next(event for event in events if event.source == "llm").error_code == "model_request_failed"
    assert "runtime-secret" not in str(events)


class FailedLlmAuditRecorder(AuditRecorder):
    def record(self, session, request):
        if request.action == "llm.invoke.failed":
            raise RuntimeError("raw failed-llm audit storage secret")
        return super().record(session, request)


def test_failed_llm_audit_failure_closes_run_without_raw_error():
    session, run_id = build_queued_run()
    repository = ConversationRepository(session)

    PlatformAgentHarness(
        repository,
        FailingGateway(),
        FakeAgentService(),
        audit_recorder=FailedLlmAuditRecorder(),
    ).execute(run_id)

    run = session.get(AgentRun, run_id)
    events = repository.list_events(run_id, 0)
    audits = list(session.scalars(select(AuditEvent)))
    assert run is not None and run.status == "failed"
    assert events[-2].payload == {
        "code": "audit_persistence_failed",
        "message": "智能体运行失败，请稍后重试",
    }
    assert events[-1].payload == {"status": "failed"}
    assert {event.action for event in audits} == {
        "agent.run.running", "agent.run.failed"
    }
    assert "raw failed-llm audit storage secret" not in str(events)
    assert "raw failed-llm audit storage secret" not in str(audits)
    assert session.scalar(select(AgentRun).where(AgentRun.id == run_id)) is not None


def test_empty_model_response_records_only_failed_llm_terminal_event():
    session, run_id = build_queued_run()
    model = ScriptedToolModel([ModelResult(content="  ")])

    PlatformAgentHarness(
        ConversationRepository(session), model, FakeAgentService()
    ).execute(run_id)

    llm_events = list(session.scalars(select(AuditEvent).where(AuditEvent.source == "llm")))
    assert [(event.action, event.status) for event in llm_events] == [
        ("llm.invoke.failed", "failed")
    ]
    assert session.get(AgentRun, run_id).status == "failed"


def test_successful_llm_tool_iteration_survives_preexecution_failure():
    session, run_id = build_queued_run()
    model = ScriptedToolModel([
        ModelResult(None, tool_calls=(ToolCall("call-1", "missing.tool", {}),)),
    ])

    PlatformAgentHarness(
        ConversationRepository(session),
        model,
        FakeAgentService(tool_ids=["missing.tool"]),
        tool_service=FakeToolService(),
    ).execute(run_id)

    llm_events = list(session.scalars(select(AuditEvent).where(AuditEvent.source == "llm")))
    assert [(event.action, event.metadata_json["iteration"]) for event in llm_events] == [
        ("llm.invoke.succeeded", 0)
    ]
    assert session.get(AgentRun, run_id).status == "failed"


class PersistentFailingRecorder(AuditRecorder):
    def record(self, session, request):
        raise RuntimeError("audit storage unavailable")


def test_running_audit_failure_leaves_failed_run_and_usable_session():
    session, run_id = build_queued_run()

    PlatformAgentHarness(
        ConversationRepository(session),
        SuccessfulGateway(),
        FakeAgentService(),
        audit_recorder=PersistentFailingRecorder(),
    ).execute(run_id)

    assert session.get(AgentRun, run_id).status == "failed"
    events = list(session.scalars(select(RunEvent).where(RunEvent.run_id == run_id)))
    assert events[-2].event_type == "run.error"
    assert events[-2].payload["code"] == "audit_persistence_failed"
    assert events[-1].payload == {"status": "failed"}
    assert session.scalar(select(AgentRun).where(AgentRun.id == run_id)) is not None


class TerminalFailingRecorder(AuditRecorder):
    def record(self, session, request):
        if request.action in {"agent.run.completed", "agent.run.failed"}:
            raise RuntimeError("terminal audit storage unavailable")
        return super().record(session, request)


def test_completed_audit_failure_does_not_leave_run_running():
    session, run_id = build_queued_run()

    PlatformAgentHarness(
        ConversationRepository(session),
        SuccessfulGateway(),
        FakeAgentService(),
        audit_recorder=TerminalFailingRecorder(),
    ).execute(run_id)

    assert session.get(AgentRun, run_id).status == "failed"
    events = list(session.scalars(select(RunEvent).where(RunEvent.run_id == run_id)))
    assert events[-2].payload["code"] == "audit_persistence_failed"
    assert events[-1].payload == {"status": "failed"}
    assert session.scalar(select(AgentRun).where(AgentRun.id == run_id)) is not None


class FailingGateway:
    def generate(
        self,
        messages: list[dict[str, str]],
        selection: ModelSelection | None = None,
        tools=None,
    ) -> ModelResult:
        raise ModelUpstreamError("upstream rejected runtime-secret")


def test_fails_run_without_exposing_upstream_error_details():
    session, run_id = build_queued_run()
    repository = ConversationRepository(session)

    PlatformAgentHarness(
        repository, FailingGateway(), FakeAgentService()
    ).execute(run_id)

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

    PlatformAgentHarness(
        repository, SuccessfulGateway(), FakeAgentService()
    ).execute(run_id)

    run = session.get(AgentRun, run_id)
    events = repository.list_events(run_id, 0)
    assert run is not None and run.status == "failed"
    assert events[-2].payload["code"] == "unsupported_actor_type"
    assert all(event.event_type != "message.completed" for event in events)


class CapturingGateway:
    def __init__(self):
        self.calls = []

    def generate(
        self,
        messages: list[dict[str, str]],
        selection: ModelSelection | None = None,
        tools=None,
    ) -> ModelResult:
        self.calls.append((messages, selection))
        return ModelResult(content="首次研判完成")


def test_run_context_stops_at_its_trigger_message():
    session, run_id = build_queued_run()
    run = session.get(AgentRun, run_id)
    assert run is not None
    session.add(
        Message(
            conversation_id=run.conversation_id,
            sequence=2,
            role="user",
            content="这是后续问题",
        )
    )
    session.commit()
    gateway = CapturingGateway()

    PlatformAgentHarness(
        ConversationRepository(session), gateway, FakeAgentService()
    ).execute(run_id)

    assert gateway.calls == [(
        [
            {"role": "system", "content": "你是洪水研判智能体"},
            {"role": "system", "content": "结合当前流域上下文"},
            {"role": "user", "content": "分析洪峰"},
        ],
        ModelSelection("deepseek", "deepseek-chat"),
    )]


def test_bounds_conversation_context_to_latest_100_eligible_messages():
    session, run_id = build_queued_run()
    run = session.get(AgentRun, run_id)
    assert run is not None

    history = [
        Message(
            conversation_id=run.conversation_id,
            sequence=index + 2,
            role="user" if index % 2 == 0 else "assistant",
            content=f"历史-{index}",
        )
        for index in range(105)
    ]
    session.add_all(history)
    session.flush()
    run.trigger_message_id = history[-1].id
    session.commit()
    gateway = CapturingGateway()

    PlatformAgentHarness(
        ConversationRepository(session), gateway, FakeAgentService()
    ).execute(run_id)

    messages, selection = gateway.calls[0]
    assert messages[:2] == [
        {"role": "system", "content": "你是洪水研判智能体"},
        {"role": "system", "content": "结合当前流域上下文"},
    ]
    assert len(messages[2:]) == 100
    assert messages[2]["content"] == "历史-5"
    assert messages[-1]["content"] == "历史-104"
    assert selection == ModelSelection("deepseek", "deepseek-chat")


def test_omits_blank_agent_prompts_and_passes_empty_model_selection():
    session, run_id = build_queued_run()
    gateway = CapturingGateway()

    PlatformAgentHarness(
        ConversationRepository(session),
        gateway,
        FakeAgentService(
            system_prompt=" ",
            context_prompt="",
            provider_id="",
            model="",
        ),
    ).execute(run_id)

    assert gateway.calls == [(
        [{"role": "user", "content": "分析洪峰"}],
        ModelSelection("", ""),
    )]


def test_fails_safely_when_agent_is_missing():
    session, run_id = build_queued_run()
    repository = ConversationRepository(session)

    PlatformAgentHarness(
        repository, CapturingGateway(), FakeAgentService(missing=True)
    ).execute(run_id)

    run = session.get(AgentRun, run_id)
    events = repository.list_events(run_id, 0)
    assert run is not None and run.status == "failed"
    assert events[-2].payload == {
        "code": "agent_unavailable",
        "message": "智能体不可用，请检查智能体配置",
    }


def test_fails_safely_when_agent_is_disabled():
    session, run_id = build_queued_run()
    repository = ConversationRepository(session)
    gateway = CapturingGateway()

    PlatformAgentHarness(
        repository, gateway, FakeAgentService(enabled=False)
    ).execute(run_id)

    run = session.get(AgentRun, run_id)
    events = repository.list_events(run_id, 0)
    assert run is not None and run.status == "failed"
    assert events[-2].payload["code"] == "agent_unavailable"
    assert gateway.calls == []
class ScriptedToolModel:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def generate(self, messages, selection=None, tools=None):
        self.calls.append((list(messages), selection, list(tools or [])))
        return self.results.pop(0)


class RecordingToolGateway:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def execute(self, call, context, authorized_tool_ids):
        self.calls.append((call, context, set(authorized_tool_ids)))
        if self.fail:
            raise self.fail
        return ToolExecutionResult(invocation_id=f"inv-{call.id}", value={"name": call.name})


class FakeToolService:
    def get(self, tool_id):
        return SimpleNamespace(
            tool_id=tool_id,
            description=tool_id,
            input_schema={"type": "object"},
            published=True,
            enabled=True,
        )


def test_executes_tool_then_persists_final_answer_with_aggregated_usage():
    session, run_id = build_queued_run()
    repository = ConversationRepository(session)
    model = ScriptedToolModel([
        ModelResult(None, 3, 2, 5, (ToolCall("call-1", "system.get_current_time", {}),)),
        ModelResult("今天是星期日", 4, 5, 9),
    ])
    tool_gateway = RecordingToolGateway()
    PlatformAgentHarness(repository, model, FakeAgentService(tool_ids=["system.get_current_time"]), tool_service=FakeToolService(), tool_gateway=tool_gateway).execute(run_id)

    run = repository.get_run_by_id(run_id)
    assert run.status == "completed"
    assert len(model.calls) == 2
    assert model.calls[1][0][-2] == {
        "role": "assistant", "content": None,
        "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "system.get_current_time", "arguments": "{}"}}],
    }
    assert model.calls[1][0][-1] == {"role": "tool", "tool_call_id": "call-1", "content": '{"name":"system.get_current_time"}'}
    context = tool_gateway.calls[0][1]
    assert (context.user_id, context.project_id, context.conversation_id, context.run_id) == ("u1", "p1", run.conversation_id, run_id)
    events = repository.list_events(run_id, 0)
    assert events[-2].payload == {"prompt_tokens": 7, "completion_tokens": 7, "total_tokens": 14}


def test_executes_multiple_tool_calls_in_order():
    session, run_id = build_queued_run()
    calls = (ToolCall("c1", "system.one", {"b": 1, "a": 2}), ToolCall("c2", "system.two", {}))
    model = ScriptedToolModel([ModelResult(None, tool_calls=calls), ModelResult("完成")])
    gateway = RecordingToolGateway()
    PlatformAgentHarness(ConversationRepository(session), model, FakeAgentService(tool_ids=["system.one", "system.two"]), tool_service=FakeToolService(), tool_gateway=gateway).execute(run_id)
    assert [item[0].id for item in gateway.calls] == ["c1", "c2"]
    assert model.calls[1][0][-2:] == [
        {"role": "tool", "tool_call_id": "c1", "content": '{"name":"system.one"}'},
        {"role": "tool", "tool_call_id": "c2", "content": '{"name":"system.two"}'},
    ]


def test_rejects_batch_exceeding_total_limit_before_any_execution():
    session, run_id = build_queued_run()
    first = tuple(ToolCall(f"c{i}", "system.one", {}) for i in range(MAX_TOOL_CALLS - 1))
    second = (ToolCall("last-1", "system.one", {}), ToolCall("last-2", "system.one", {}))
    model = ScriptedToolModel([ModelResult(None, tool_calls=first), ModelResult(None, tool_calls=second)])
    gateway = RecordingToolGateway()
    repo = ConversationRepository(session)
    PlatformAgentHarness(repo, model, FakeAgentService(tool_ids=["system.one"]), tool_service=FakeToolService(), tool_gateway=gateway).execute(run_id)
    assert len(gateway.calls) == MAX_TOOL_CALLS - 1
    assert repo.list_events(run_id, 0)[-2].payload == {"code": "tool_iteration_limit", "message": "工具调用次数超过平台限制"}
    assert not any(message.role == "assistant" for message in repo.get_run_messages(run_id))


def test_fails_after_fourth_model_iteration_requests_more_tools():
    session, run_id = build_queued_run()
    model = ScriptedToolModel([ModelResult(None, tool_calls=(ToolCall(f"c{i}", "system.one", {}),)) for i in range(MAX_MODEL_ITERATIONS)])
    gateway = RecordingToolGateway()
    repo = ConversationRepository(session)
    PlatformAgentHarness(repo, model, FakeAgentService(tool_ids=["system.one"]), tool_service=FakeToolService(), tool_gateway=gateway).execute(run_id)
    assert len(model.calls) == MAX_MODEL_ITERATIONS
    assert len(gateway.calls) == MAX_MODEL_ITERATIONS - 1
    assert repo.list_events(run_id, 0)[-2].payload["code"] == "tool_iteration_limit"


def test_tool_runtime_error_fails_safely_without_assistant_message():
    session, run_id = build_queued_run()
    repo = ConversationRepository(session)
    model = ScriptedToolModel([ModelResult(None, tool_calls=(ToolCall("c1", "system.one", {}),))])
    error = ToolRuntimeError("tool_not_authorized", "该工具当前不可用。")
    PlatformAgentHarness(repo, model, FakeAgentService(tool_ids=["system.one"]), tool_service=FakeToolService(), tool_gateway=RecordingToolGateway(error)).execute(run_id)
    assert repo.get_run_by_id(run_id).status == "failed"
    assert repo.list_events(run_id, 0)[-2].payload == {"code": "tool_not_authorized", "message": "该工具当前不可用。"}
    assert not any(message.role == "assistant" for message in repo.get_run_messages(run_id))


def test_approval_required_pauses_run_without_recording_failure():
    session, run_id = build_queued_run()
    repo = ConversationRepository(session)
    model = ScriptedToolModel([ModelResult(None, tool_calls=(ToolCall("c1", "system.one", {}),))])
    error = ToolRuntimeError("approval_required", "该工具需要人工审批后才能执行。")
    PlatformAgentHarness(repo, model, FakeAgentService(tool_ids=["system.one"]), tool_service=FakeToolService(), tool_gateway=RecordingToolGateway(error)).execute(run_id)
    assert repo.get_run_by_id(run_id).status == "waiting_approval"
    assert repo.list_events(run_id, 0)[-1].payload == {"status": "waiting_approval"}
    assert not any(event.event_type == "run.error" for event in repo.list_events(run_id, 0))


def test_approval_required_persists_checkpoint_state():
    class Checkpoints:
        def __init__(self): self.saved = []
        def save(self, run_id, checkpoint_key, state, *, commit=True):
            self.saved.append((run_id, checkpoint_key, state))

    session, run_id = build_queued_run()
    repo = ConversationRepository(session)
    model = ScriptedToolModel([ModelResult(None, tool_calls=(ToolCall("c1", "system.one", {}),))])
    checkpoint = Checkpoints()
    error = ToolRuntimeError("approval_required", "该工具需要人工审批后才能执行。")
    PlatformAgentHarness(repo, model, FakeAgentService(tool_ids=["system.one"]), tool_service=FakeToolService(), tool_gateway=RecordingToolGateway(error), checkpoint_store=checkpoint).execute(run_id)

    assert checkpoint.saved[0][0:2] == (run_id, "approval")
    assert checkpoint.saved[0][2]["status"] == "waiting_approval"
    assert checkpoint.saved[0][2]["messages"][-1]["role"] == "assistant"


def test_completed_run_persists_terminal_checkpoint_state():
    class Checkpoints:
        def __init__(self): self.saved = []
        def save(self, run_id, checkpoint_key, state, *, commit=True):
            self.saved.append((run_id, checkpoint_key, state))

    session, run_id = build_queued_run()
    checkpoint = Checkpoints()
    PlatformAgentHarness(
        ConversationRepository(session), SuccessfulGateway(), FakeAgentService(),
        checkpoint_store=checkpoint,
    ).execute(run_id)

    assert checkpoint.saved[-1][0:2] == (run_id, "runtime")
    assert checkpoint.saved[-1][2]["status"] == "completed"
    assert checkpoint.saved[-1][2]["messages"][-1] == {"role": "assistant", "content": "研判完成"}


def build_integrated_runtime(tmp_path, *, enabled=True, bound=True):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'integrated-runtime.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    store = ToolStore(factory)
    service = ToolService(store)
    if not enabled:
        store.set_enabled("system.get_current_time", False)
    session = factory()
    conversation = Conversation(
        unit_id="trusted-unit",
        project_id="trusted-project",
        owner_id="trusted-user",
        title="时间",
    )
    session.add(conversation)
    session.flush()
    message = Message(conversation_id=conversation.id, role="user", content="今天星期几？")
    session.add(message)
    session.flush()
    run = AgentRun(
        conversation_id=conversation.id,
        trigger_message_id=message.id,
        actor_type="agent",
        actor_id="flood",
        actor_roles_json=["project_admin", "user"],
        status="queued",
    )
    session.add(run)
    session.flush()
    session.add(RunEvent(run_id=run.id, sequence=1, event_type="run.status", payload={"status": "queued"}))
    session.commit()
    gateway = ToolGateway(
        tool_store=store,
        repository=ConversationRepository(session),
        clock=lambda: datetime(2026, 8, 2, 0, 0, tzinfo=timezone.utc),
    )
    agent = FakeAgentService(tool_ids=["system.get_current_time"] if bound else [])
    return session, run.id, service, gateway, agent


def test_integrated_time_tool_loop_persists_events_and_trusted_context(tmp_path):
    session, run_id, service, gateway, agent = build_integrated_runtime(tmp_path)
    repository = ConversationRepository(session)
    model = ScriptedToolModel([
        ModelResult(None, 3, 1, 4, (ToolCall("time-1", "system.get_current_time", {}),)),
        ModelResult("今天是星期日", 5, 2, 7),
    ])

    PlatformAgentHarness(
        repository,
        model,
        agent,
        tool_service=service,
        tool_gateway=gateway,
    ).execute(run_id)

    tool_message = model.calls[1][0][-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "time-1"
    assert '"timezone":"Asia/Shanghai"' in tool_message["content"]
    assert '"weekday_zh":"星期日"' in tool_message["content"]
    assert [event.event_type for event in repository.list_events(run_id, 0)] == [
        "run.status",
        "run.status",
        "tool.started",
        "tool.completed",
        "message.completed",
        "run.usage",
        "run.status",
    ]
    invocation = repository.list_tool_invocations(run_id)[0]
    assert invocation.status == "completed"
    assert invocation.tool_id == "system.get_current_time"
    context = repository.get_run_execution_context(run_id)
    assert context == {
        "run_id": run_id,
        "conversation_id": repository.get_run_by_id(run_id).conversation_id,
        "unit_id": "trusted-unit",
        "project_id": "trusted-project",
        "user_id": "trusted-user",
        "actor_roles": ("project_admin", "user"),
    }
    audits = list(session.scalars(select(AuditEvent).order_by(AuditEvent.occurred_at)))
    assert {tuple(event.actor_roles_json) for event in audits} == {
        ("project_admin", "user")
    }
    session.close()


def test_integrated_gateway_rejects_unbound_tool_without_invocation(tmp_path):
    session, run_id, service, gateway, agent = build_integrated_runtime(tmp_path, bound=False)
    repository = ConversationRepository(session)
    model = ScriptedToolModel([
        ModelResult(None, tool_calls=(ToolCall("time-unbound", "system.get_current_time", {}),)),
    ])
    PlatformAgentHarness(repository, model, agent, tool_service=service, tool_gateway=gateway).execute(run_id)
    assert repository.get_run_by_id(run_id).status == "failed"
    assert repository.list_tool_invocations(run_id) == []
    assert not any(event.event_type.startswith("tool.") for event in repository.list_events(run_id, 0))
    assert not any(message.role == "assistant" for message in repository.get_run_messages(run_id))
    session.close()


def test_integrated_gateway_rejects_disabled_tool_without_invocation(tmp_path):
    session, run_id, service, gateway, agent = build_integrated_runtime(tmp_path, enabled=False)
    repository = ConversationRepository(session)
    model = ScriptedToolModel([
        ModelResult(None, tool_calls=(ToolCall("time-disabled", "system.get_current_time", {}),)),
    ])
    PlatformAgentHarness(repository, model, agent, tool_service=service, tool_gateway=gateway).execute(run_id)
    assert repository.get_run_by_id(run_id).status == "failed"
    assert repository.list_events(run_id, 0)[-2].payload["code"] == "tool_not_authorized"
    assert repository.list_tool_invocations(run_id) == []
    assert not any(event.event_type.startswith("tool.") for event in repository.list_events(run_id, 0))
    assert not any(message.role == "assistant" for message in repository.get_run_messages(run_id))
    session.close()

def test_integrated_final_model_round_does_not_execute_returned_tool(tmp_path):
    session, run_id, service, gateway, agent = build_integrated_runtime(tmp_path)
    repository = ConversationRepository(session)
    model = ScriptedToolModel([
        ModelResult(
            None,
            tool_calls=(ToolCall(
                f"final-round-{index}",
                "system.get_current_time",
                {},
            ),),
        )
        for index in range(MAX_MODEL_ITERATIONS)
    ])

    PlatformAgentHarness(
        repository,
        model,
        agent,
        tool_service=service,
        tool_gateway=gateway,
    ).execute(run_id)

    assert repository.get_run_by_id(run_id).status == "failed"
    invocations = repository.list_tool_invocations(run_id)
    assert {item.tool_call_id for item in invocations} == {
        "final-round-0",
        "final-round-1",
        "final-round-2",
    }
    assert all(item.tool_call_id != "final-round-3" for item in invocations)
    tool_events = [
        event
        for event in repository.list_events(run_id, 0)
        if event.event_type.startswith("tool.")
    ]
    assert len(tool_events) == 2 * (MAX_MODEL_ITERATIONS - 1)
    assert repository.list_events(run_id, 0)[-2].payload["code"] == (
        "tool_iteration_limit"
    )
    session.close()
