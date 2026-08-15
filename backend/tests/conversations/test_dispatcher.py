from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.approvals.models import Approval
from app.approvals.service import arguments_digest
from app.conversations.dispatcher import (
    SandboxRunDispatcher,
    ThreadRunDispatcher,
    build_default_run_dispatcher,
)
from app.conversations.models import (
    AgentRun,
    Conversation,
    Message,
    RunEvent,
    ToolInvocation,
)
from app.db.base import Base
from app.db.platform_models import RegisteredToolRecord
from app.runtime.checkpoint_store import RuntimeCheckpoint
from app.runtime.model_gateway import ModelResult, ModelSelection
from app.tools.builtins import BUILTIN_TOOL_DEFINITIONS
from app.tools.schemas import ToolCall, ToolRuntimeError
from app.tools.store import ToolStore


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


def test_thread_dispatcher_does_not_enable_artifact_storage_by_default(monkeypatch):
    monkeypatch.setattr(
        "app.conversations.dispatcher.S3ObjectStorage",
        lambda: (_ for _ in ()).throw(RuntimeError("storage should be explicit")),
    )
    dispatcher = ThreadRunDispatcher(max_workers=1)

    assert dispatcher.artifact_storage_factory() is None

    dispatcher.shutdown()


def test_dispatches_run_with_an_independent_database_session(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'runtime.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    gateway = RecordingGateway()

    with factory() as request_session:
        conversation = Conversation(
            unit_id="unit-1",
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


def test_sandbox_dispatcher_runs_coordinator_in_background():
    calls = []
    coordinator = SimpleNamespace(execute=lambda run_id: calls.append(run_id))
    dispatcher = SandboxRunDispatcher(coordinator, max_workers=1)

    dispatcher.dispatch("run-1")
    dispatcher.shutdown()

    assert calls == ["run-1"]


def test_sandbox_dispatcher_recovers_running_runs_on_startup():
    calls = []
    coordinator = SimpleNamespace(
        execute=lambda run_id: None,
        list_recoverable_run_ids=lambda: ["run-1", "run-2"],
        list_cleanup_retry_run_ids=lambda: ["run-3"],
        recover=lambda run_id: calls.append(run_id),
        retry_cleanup=lambda run_id: calls.append(f"cleanup:{run_id}"),
    )
    dispatcher = SandboxRunDispatcher(coordinator, max_workers=1, recover_on_startup=True)

    dispatcher.shutdown()

    assert calls == ["run-1", "run-2", "cleanup:run-3"]


def test_default_dispatcher_keeps_local_harness_unless_sandbox_is_explicitly_enabled(monkeypatch):
    monkeypatch.setattr("app.conversations.dispatcher.workflow_runner_client_from_env", lambda: None)
    assert isinstance(build_default_run_dispatcher(), ThreadRunDispatcher)

    runner = SimpleNamespace()
    monkeypatch.setattr("app.conversations.dispatcher.workflow_runner_client_from_env", lambda: runner)
    monkeypatch.setenv("IAP_RUNNER_TOKEN_SIGNING_KEY", "x" * 32)
    monkeypatch.setenv(
        "IAP_RUNNER_GATEWAY_URL",
        "http://api:8000/internal/runner",
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    dispatcher = build_default_run_dispatcher(session_factory=factory)
    assert isinstance(dispatcher, SandboxRunDispatcher)
    assert dispatcher.coordinator.snapshot_service_factory is not None
    assert dispatcher.coordinator.token_service_factory is not None
    assert dispatcher.coordinator.gateway_url == "http://api:8000/internal/runner"
    dispatcher.shutdown()


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
        conversation = Conversation(unit_id="unit-1", project_id="p-tool", owner_id="u-tool", title="时间")
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


def test_dispatcher_resumes_approved_tool_and_completes_run(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'dispatcher-approval.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)

    class ResumeModel:
        def __init__(self): self.calls = []
        def generate(self, messages, selection=None, tools=None):
            self.calls.append(list(messages))
            return ModelResult("审批后的结果")

    model = ResumeModel()
    with factory.begin() as session:
        store = ToolStore(factory)
        for definition in BUILTIN_TOOL_DEFINITIONS:
            store.upsert_builtin(definition)
        tool = session.get(RegisteredToolRecord, "system.get_current_time")
        tool.requires_approval = True
        tool.risk_level = "high"
        conversation = Conversation(unit_id="unit-1", project_id="p-tool", owner_id="u-tool", title="审批")
        session.add(conversation); session.flush()
        message = Message(conversation_id=conversation.id, role="user", content="查询时间")
        session.add(message); session.flush()
        run = AgentRun(conversation_id=conversation.id, trigger_message_id=message.id, actor_type="agent", actor_id="flood", actor_roles_json=["user"], status="queued")
        session.add(run); session.flush()
        invocation = ToolInvocation(run_id=run.id, tool_call_id="approval-call", tool_id="system.get_current_time", tool_version="1.0.0", status="waiting_approval", arguments_summary={})
        session.add(invocation); session.flush()
        approval = Approval(run_id=run.id, invocation_id=invocation.id, tool_id=invocation.tool_id, tool_version=invocation.tool_version, unit_id="unit-1", project_id="p-tool", requester_id="u-tool", requester_roles=["user"], assignee_role="project_admin", risk_level="high", arguments_summary={}, arguments_digest=arguments_digest({}), status="approved", expires_at=datetime(2026, 8, 11, tzinfo=timezone.utc))
        session.add(approval); session.flush()
        run_id = run.id

    dispatcher = ThreadRunDispatcher(session_factory=factory, gateway_factory=lambda: model, agent_service_factory=ToolBoundAgentService, max_workers=1)
    dispatcher.resume_approval(approval.id)
    dispatcher.shutdown()

    with factory() as session:
        assert session.get(AgentRun, run_id).status == "completed"
        assert session.query(ToolInvocation).filter_by(run_id=run_id).one().status == "completed"
        assert session.query(Message).filter_by(conversation_id=conversation.id).order_by(Message.sequence).all()[-1].content == "审批后的结果"
        checkpoint = session.query(RuntimeCheckpoint).filter_by(run_id=run_id).order_by(RuntimeCheckpoint.updated_at.desc()).first()
        assert checkpoint.state["status"] == "completed"
    assert model.calls[0][-1]["role"] == "tool"


def test_sandbox_dispatcher_executes_approved_tool_before_resuming_run(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'sandbox-dispatcher-approval.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)

    with factory.begin() as session:
        store = ToolStore(factory)
        for definition in BUILTIN_TOOL_DEFINITIONS:
            store.upsert_builtin(definition)
        tool = session.get(RegisteredToolRecord, "system.get_current_time")
        tool.requires_approval = True
        conversation = Conversation(
            unit_id="unit-1",
            project_id="p-tool",
            owner_id="u-tool",
            title="沙箱审批",
        )
        session.add(conversation)
        session.flush()
        message = Message(
            conversation_id=conversation.id,
            role="user",
            content="查询时间",
        )
        session.add(message)
        session.flush()
        run = AgentRun(
            conversation_id=conversation.id,
            trigger_message_id=message.id,
            actor_type="agent",
            actor_id="flood",
            actor_roles_json=["user"],
            status="queued",
        )
        session.add(run)
        session.flush()
        invocation = ToolInvocation(
            run_id=run.id,
            tool_call_id="sandbox-approval-call",
            tool_id="system.get_current_time",
            tool_version="1.0.0",
            status="waiting_approval",
            arguments_summary={},
        )
        session.add(invocation)
        session.flush()
        approval = Approval(
            run_id=run.id,
            invocation_id=invocation.id,
            tool_id=invocation.tool_id,
            tool_version=invocation.tool_version,
            unit_id="unit-1",
            project_id="p-tool",
            requester_id="u-tool",
            requester_roles=["user"],
            assignee_role="project_admin",
            risk_level="high",
            arguments_summary={},
            arguments_digest=arguments_digest({}),
            status="approved",
            expires_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
        )
        session.add(approval)
        session.flush()
        approval_id = approval.id
        run_id = run.id

    class RecordingCoordinator:
        def __init__(self):
            self.executed = []

        def execute(self, current_run_id):
            self.executed.append(current_run_id)

    coordinator = RecordingCoordinator()
    dispatcher = SandboxRunDispatcher(
        coordinator,
        session_factory=factory,
        max_workers=1,
    )
    dispatcher.resume_approval(approval_id)
    dispatcher.resume_approval(approval_id)
    dispatcher.shutdown()

    with factory() as session:
        assert session.get(ToolInvocation, invocation.id).status == "completed"
        assert session.get(AgentRun, run_id).status == "queued"
    assert coordinator.executed == [run_id]


def test_sandbox_dispatcher_does_not_resume_a_run_cancelled_before_worker_claim(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'sandbox-cancelled-approval.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)

    with factory.begin() as session:
        store = ToolStore(factory)
        for definition in BUILTIN_TOOL_DEFINITIONS:
            store.upsert_builtin(definition)
        conversation = Conversation(
            unit_id="unit-1",
            project_id="p-tool",
            owner_id="u-tool",
            title="取消后的审批",
        )
        session.add(conversation)
        session.flush()
        message = Message(
            conversation_id=conversation.id,
            role="user",
            content="查询时间",
        )
        session.add(message)
        session.flush()
        run = AgentRun(
            conversation_id=conversation.id,
            trigger_message_id=message.id,
            actor_type="agent",
            actor_id="flood",
            actor_roles_json=["user"],
            status="cancelled",
        )
        session.add(run)
        session.flush()
        invocation = ToolInvocation(
            run_id=run.id,
            tool_call_id="cancelled-approval-call",
            tool_id="system.get_current_time",
            tool_version="1.0.0",
            status="waiting_approval",
            arguments_summary={},
        )
        session.add(invocation)
        session.flush()
        approval = Approval(
            run_id=run.id,
            invocation_id=invocation.id,
            tool_id=invocation.tool_id,
            tool_version=invocation.tool_version,
            unit_id="unit-1",
            project_id="p-tool",
            requester_id="u-tool",
            requester_roles=["user"],
            assignee_role="project_admin",
            risk_level="high",
            arguments_summary={},
            arguments_digest=arguments_digest({}),
            status="approved",
            expires_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
        )
        session.add(approval)
        session.flush()
        approval_id = approval.id
        run_id = run.id
        invocation_id = invocation.id

    coordinator = SimpleNamespace(executed=[])
    coordinator.execute = coordinator.executed.append
    dispatcher = SandboxRunDispatcher(
        coordinator,
        session_factory=factory,
        max_workers=1,
    )
    dispatcher.resume_approval(approval_id)
    dispatcher.shutdown()

    with factory() as session:
        assert session.get(AgentRun, run_id).status == "cancelled"
        assert session.get(ToolInvocation, invocation_id).status == "waiting_approval"
    assert coordinator.executed == []


def test_sandbox_dispatcher_marks_run_failed_when_approved_tool_fails(
    tmp_path, monkeypatch
):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'sandbox-approval-failure.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)

    with factory.begin() as session:
        store = ToolStore(factory)
        for definition in BUILTIN_TOOL_DEFINITIONS:
            store.upsert_builtin(definition)
        conversation = Conversation(
            unit_id="unit-1",
            project_id="p-tool",
            owner_id="u-tool",
            title="审批工具失败",
        )
        session.add(conversation)
        session.flush()
        message = Message(
            conversation_id=conversation.id,
            role="user",
            content="查询时间",
        )
        session.add(message)
        session.flush()
        run = AgentRun(
            conversation_id=conversation.id,
            trigger_message_id=message.id,
            actor_type="agent",
            actor_id="flood",
            actor_roles_json=["user"],
            status="queued",
        )
        session.add(run)
        session.flush()
        invocation = ToolInvocation(
            run_id=run.id,
            tool_call_id="failed-approval-call",
            tool_id="system.get_current_time",
            tool_version="1.0.0",
            status="waiting_approval",
            arguments_summary={},
        )
        session.add(invocation)
        session.flush()
        approval = Approval(
            run_id=run.id,
            invocation_id=invocation.id,
            tool_id=invocation.tool_id,
            tool_version=invocation.tool_version,
            unit_id="unit-1",
            project_id="p-tool",
            requester_id="u-tool",
            requester_roles=["user"],
            assignee_role="project_admin",
            risk_level="high",
            arguments_summary={},
            arguments_digest=arguments_digest({}),
            status="approved",
            expires_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
        )
        session.add(approval)
        session.flush()
        approval_id = approval.id
        run_id = run.id

    def fail_approved_tool(self, current_approval_id, context):
        raise ToolRuntimeError("tool_execution_failed", "工具执行失败。")

    monkeypatch.setattr(
        "app.conversations.dispatcher.ToolGateway.execute_approved",
        fail_approved_tool,
    )
    coordinator = SimpleNamespace(executed=[])
    coordinator.execute = coordinator.executed.append
    dispatcher = SandboxRunDispatcher(
        coordinator,
        session_factory=factory,
        max_workers=1,
    )
    dispatcher.resume_approval(approval_id)
    dispatcher.shutdown()

    with factory() as session:
        assert session.get(AgentRun, run_id).status == "failed"
        error = session.scalar(
            select(RunEvent)
            .where(RunEvent.run_id == run_id, RunEvent.event_type == "run.error")
            .order_by(RunEvent.sequence.desc())
        )
        assert error.payload == {
            "code": "tool_execution_failed",
            "message": "工具执行失败。",
        }
    assert coordinator.executed == []
