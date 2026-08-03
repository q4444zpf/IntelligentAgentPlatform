from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.audit.models import AuditEvent
from app.audit.recorder import AuditRecorder
from app.conversations.models import AgentRun, Conversation, Message, RunEvent, ToolInvocation
from app.conversations.repository import ConversationRepository
from app.db.base import Base
from app.db.platform_models import RegisteredToolRecord
from app.tools.builtins import BUILTIN_EXECUTORS, BUILTIN_TOOL_DEFINITIONS
from app.tools.gateway import ToolGateway
from app.tools.schemas import ToolCall, ToolExecutionContext, ToolRuntimeError
from app.tools.store import ToolStore


@pytest.fixture
def runtime(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'gateway.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    store = ToolStore(factory)
    for definition in BUILTIN_TOOL_DEFINITIONS:
        store.upsert_builtin(definition)
    with factory.begin() as session:
        conversation = Conversation(
            id="conversation-1",
            unit_id="unit-1",
            project_id="project-1",
            owner_id="user-1",
            title="test",
        )
        message = Message(id="message-1", conversation_id=conversation.id, sequence=1, role="user", content="time")
        run = AgentRun(id="run-1", conversation_id=conversation.id, trigger_message_id=message.id, actor_type="agent", actor_id="agent-1", status="running")
        session.add_all([conversation, message, run])
    return factory, store


def make_gateway(runtime, now=datetime(2026, 8, 2, 4, 30, tzinfo=timezone.utc)):
    factory, store = runtime
    session = factory()
    return session, ToolGateway(tool_store=store, repository=ConversationRepository(session), clock=lambda: now)


def context(**overrides):
    values = dict(
        run_id="run-1",
        conversation_id="conversation-1",
        unit_id="unit-1",
        project_id="project-1",
        user_id="user-1",
        timezone="Asia/Shanghai",
    )
    values.update(overrides)
    return ToolExecutionContext(**values)


def execute(gateway, name="system.get_current_time", arguments=None, call_id="call-1", authorized=None, execution_context=None):
    return gateway.execute(ToolCall(id=call_id, name=name, arguments=arguments or {}), execution_context or context(), authorized if authorized is not None else {name})


def test_records_tool_started_and_succeeded_with_context_and_parent(runtime):
    session, gateway = make_gateway(runtime)
    result = execute(gateway)
    events = list(session.scalars(select(AuditEvent).order_by(AuditEvent.occurred_at, AuditEvent.id)))
    assert [event.action for event in events] == ["tool.invoke.started", "tool.invoke.succeeded"]
    assert events[1].parent_event_id == events[0].id
    assert events[0].idempotency_key == f"tool:{result.invocation_id}:started"
    assert (events[0].unit_id, events[0].project_id, events[0].user_id) == ("unit-1", "project-1", "user-1")


def test_records_tool_failure_without_arguments_or_raw_error(runtime, monkeypatch):
    session, gateway = make_gateway(runtime)
    def fail(*_args):
        raise RuntimeError("secret raw failure")
    monkeypatch.setitem(BUILTIN_EXECUTORS, "system.get_current_time", fail)
    with pytest.raises(ToolRuntimeError):
        execute(gateway, arguments={"timezone": "Asia/Shanghai"})
    events = list(session.scalars(select(AuditEvent).order_by(AuditEvent.occurred_at, AuditEvent.id)))
    assert [event.action for event in events] == ["tool.invoke.started", "tool.invoke.failed"]
    assert events[-1].error_code == "tool_execution_failed"
    assert "Asia/Shanghai" not in str(events)
    assert "secret raw failure" not in str(events)


def test_current_time_uses_frozen_clock_and_chinese_weekday(runtime):
    session, gateway = make_gateway(runtime)
    try:
        assert execute(gateway).value == {"iso_datetime": "2026-08-02T12:30:00+08:00", "date": "2026-08-02", "time": "12:30:00", "weekday": "Sunday", "weekday_zh": "星期日", "timezone": "Asia/Shanghai"}
    finally:
        session.close()


def test_current_time_accepts_other_iana_timezone(runtime):
    session, gateway = make_gateway(runtime)
    try:
        result = execute(gateway, arguments={"timezone": "America/New_York"})
        assert result.value["iso_datetime"] == "2026-08-02T00:30:00-04:00"
        assert result.value["timezone"] == "America/New_York"
    finally:
        session.close()


@pytest.mark.parametrize("arguments", [{"timezone": "Mars/Olympus"}, {"extra": True}])
def test_invalid_arguments_fail_safely(runtime, arguments):
    session, gateway = make_gateway(runtime)
    try:
        with pytest.raises(ToolRuntimeError) as caught:
            execute(gateway, arguments=arguments)
        assert (caught.value.code, caught.value.safe_message) == ("tool_invalid_arguments", "工具参数无效。")
    finally:
        session.close()


def test_runtime_context_uses_only_server_context(runtime):
    session, gateway = make_gateway(runtime)
    try:
        result = execute(gateway, name="system.get_runtime_context", execution_context=context(user_id="trusted-user"))
        assert result.value == {"current_time": "2026-08-02T12:30:00+08:00", "timezone": "Asia/Shanghai", "user_id": "trusted-user", "project_id": "project-1", "conversation_id": "conversation-1", "run_id": "run-1"}
        with pytest.raises(ToolRuntimeError) as caught:
            execute(gateway, name="system.get_runtime_context", arguments={"user_id": "attacker"}, call_id="call-2")
        assert caught.value.code == "tool_invalid_arguments"
    finally:
        session.close()


@pytest.mark.parametrize(("name", "authorized", "mutation"), [("system.get_current_time", set(), None), ("system.get_current_time", {"system.get_current_time"}, "disable"), ("system.get_current_time", {"system.get_current_time"}, "unpublish"), ("system.unknown", {"system.unknown"}, None)])
def test_unavailable_tools_are_not_authorized(runtime, name, authorized, mutation):
    factory, _ = runtime
    if mutation:
        with factory.begin() as db:
            row = db.get(RegisteredToolRecord, name)
            setattr(row, "enabled" if mutation == "disable" else "published", False)
    session, gateway = make_gateway(runtime)
    try:
        with pytest.raises(ToolRuntimeError) as caught:
            execute(gateway, name=name, authorized=authorized)
        assert (caught.value.code, caught.value.safe_message) == ("tool_not_authorized", "该工具当前不可用。")
    finally:
        session.close()


def test_invalid_registered_output_schema_fails_closed(runtime):
    factory, _ = runtime
    with factory.begin() as db:
        db.get(RegisteredToolRecord, "system.get_current_time").output_schema = {"type": "object", "required": ["impossible"]}
    session, gateway = make_gateway(runtime)
    try:
        with pytest.raises(ToolRuntimeError) as caught:
            execute(gateway)
        assert (caught.value.code, caught.value.safe_message) == ("tool_execution_failed", "工具执行失败。")
    finally:
        session.close()


def test_audit_redacts_and_bounds_nested_summaries(runtime):
    session, gateway = make_gateway(runtime)
    try:
        summary = gateway.summarize({"authorization": "Bearer private", "nested": {"api_key": "private", "values": list(range(40))}, "object": {f"k{i}": i for i in range(70)}, "deep": {"a": {"b": {"c": {"d": {"e": {"password": "private"}}}}}}, "large": "x" * 10_000})
        assert summary["authorization"] == "[REDACTED]"
        assert summary["nested"]["api_key"] == "[REDACTED]"
        assert len(summary["nested"]["values"]) == 20
        assert len(summary["object"]) == 50
        assert summary["deep"]["a"]["b"]["c"]["d"] == "[TRUNCATED]"
        assert len(gateway.serialize_summary(summary).encode("utf-8")) <= 4096
    finally:
        session.close()


def test_success_persists_invocation_and_ordered_safe_events(runtime):
    session, gateway = make_gateway(runtime)
    try:
        result = execute(gateway)
        events = list(session.scalars(select(RunEvent).order_by(RunEvent.sequence)))
        invocation = session.scalar(select(ToolInvocation))
        assert invocation.id == result.invocation_id and invocation.status == "completed"
        assert invocation.error_code is None
        assert invocation.result_summary["timezone"] == "Asia/Shanghai"
        assert [event.event_type for event in events] == ["tool.started", "tool.completed"]
        assert events[0].payload == {"invocation_id": invocation.id, "tool_id": invocation.tool_id, "display_name": "获取当前时间"}
        assert set(events[1].payload) == {"invocation_id", "tool_id", "display_name", "duration_ms"}
    finally:
        session.close()


def test_failure_closes_invocation_and_emits_safe_failed_event(runtime):
    session, gateway = make_gateway(runtime)
    try:
        with pytest.raises(ToolRuntimeError):
            execute(gateway, arguments={"timezone": "Private/LeakedZone"})
        invocation = session.scalar(select(ToolInvocation))
        events = list(session.scalars(select(RunEvent).order_by(RunEvent.sequence)))
        assert invocation.status == "failed"
        assert invocation.error_code == "tool_invalid_arguments"
        assert [event.event_type for event in events] == ["tool.started", "tool.failed"]
        assert events[-1].payload["code"] == "tool_invalid_arguments"
        assert events[-1].payload["message"] == "工具参数无效。"
        assert "Private/LeakedZone" not in str(events[-1].payload)
    finally:
        session.close()


def test_duplicate_call_id_is_rejected_without_second_audit(runtime):
    session, gateway = make_gateway(runtime)
    try:
        first = execute(gateway)
        with pytest.raises(ToolRuntimeError) as caught:
            execute(gateway)
        assert caught.value.code == "tool_duplicate_call"
        assert session.query(ToolInvocation).count() == 1
        assert session.query(RunEvent).count() == 2
        assert first.invocation_id
    finally:
        session.close()

def test_invalid_registered_input_schema_is_execution_failure(runtime):
    factory, _ = runtime
    with factory.begin() as db:
        db.get(RegisteredToolRecord, "system.get_current_time").input_schema = {
            "type": "not-a-json-schema-type"
        }
    session, gateway = make_gateway(runtime)
    try:
        with pytest.raises(ToolRuntimeError) as caught:
            execute(gateway)
        assert caught.value.code == "tool_execution_failed"
        assert session.query(ToolInvocation).count() == 0
    finally:
        session.close()


def test_missing_builtin_executor_is_execution_failure(runtime, monkeypatch):
    session, gateway = make_gateway(runtime)
    monkeypatch.delitem(BUILTIN_EXECUTORS, "system.get_current_time")
    try:
        with pytest.raises(ToolRuntimeError) as caught:
            execute(gateway)
        assert caught.value.code == "tool_execution_failed"
        assert session.query(ToolInvocation).count() == 0
    finally:
        session.close()


def test_nonduplicate_integrity_error_when_starting_is_execution_failure(
    runtime, monkeypatch
):
    session, gateway = make_gateway(runtime)
    monkeypatch.setattr(
        gateway,
        "_commit_started",
        lambda *_args: (_ for _ in ()).throw(
            IntegrityError("insert", {}, Exception("foreign key"))
        ),
    )
    try:
        with pytest.raises(ToolRuntimeError) as caught:
            execute(gateway)
        assert caught.value.code == "tool_execution_failed"
    finally:
        session.close()


def test_started_integrity_error_is_duplicate_only_when_call_exists(
    runtime, monkeypatch
):
    session, gateway = make_gateway(runtime)
    checks = iter([None, ToolInvocation(run_id="run-1", tool_call_id="call-1")])
    monkeypatch.setattr(gateway.repository, "get_tool_invocation", lambda *_: next(checks))
    monkeypatch.setattr(
        gateway,
        "_commit_started",
        lambda *_args: (_ for _ in ()).throw(
            IntegrityError("insert", {}, Exception("unique"))
        ),
    )
    try:
        with pytest.raises(ToolRuntimeError) as caught:
            execute(gateway)
        assert caught.value.code == "tool_duplicate_call"
    finally:
        session.close()


class OneShotTerminalFailingRecorder(AuditRecorder):
    def __init__(self):
        self.failed = False

    def record(self, session, request):
        if request.action == "tool.invoke.succeeded" and not self.failed:
            self.failed = True
            raise RuntimeError("one-shot terminal audit failure")
        return super().record(session, request)


def test_success_terminal_audit_failure_retries_true_outcome_without_duplicates(runtime):
    factory, store = runtime
    session = factory()
    gateway = ToolGateway(
        tool_store=store,
        repository=ConversationRepository(session),
        audit_recorder=OneShotTerminalFailingRecorder(),
    )

    result = execute(gateway)

    invocation = session.get(ToolInvocation, result.invocation_id)
    audits = list(session.scalars(select(AuditEvent).order_by(AuditEvent.occurred_at)))
    events = list(session.scalars(select(RunEvent).order_by(RunEvent.sequence)))
    assert invocation.status == "completed"
    assert invocation.result_summary["timezone"] == "Asia/Shanghai"
    assert invocation.error_code is None
    assert [event.action for event in audits] == [
        "tool.invoke.started", "tool.invoke.succeeded"
    ]
    assert audits[1].parent_event_id == audits[0].id
    assert [event.event_type for event in events] == ["tool.started", "tool.completed"]


class PersistentTerminalFailingRecorder(AuditRecorder):
    def record(self, session, request):
        if request.action == "tool.invoke.succeeded":
            raise RuntimeError("persistent terminal audit failure")
        return super().record(session, request)


def test_persistent_success_audit_failure_preserves_external_success(runtime):
    factory, store = runtime
    session = factory()
    gateway = ToolGateway(
        tool_store=store,
        repository=ConversationRepository(session),
        audit_recorder=PersistentTerminalFailingRecorder(),
    )

    with pytest.raises(ToolRuntimeError) as caught:
        execute(gateway)

    assert caught.value.code == "tool_execution_failed"
    invocation = session.scalar(select(ToolInvocation))
    assert invocation.status == "completed"
    assert invocation.result_summary["timezone"] == "Asia/Shanghai"
    assert invocation.error_code is None


def test_completion_commit_failure_compensates_started_invocation(
    runtime, monkeypatch
):
    session, gateway = make_gateway(runtime)
    real_commit = session.commit
    commit_count = 0

    def fail_completion_once():
        nonlocal commit_count
        commit_count += 1
        if commit_count == 2:
            raise IntegrityError("commit", {}, Exception("event conflict"))
        real_commit()

    monkeypatch.setattr(session, "commit", fail_completion_once)
    try:
        result = execute(gateway)
        invocation = session.scalar(select(ToolInvocation))
        assert result.invocation_id == invocation.id
        assert invocation.status == "completed"
        assert invocation.error_code is None
        assert invocation.result_summary["timezone"] == "Asia/Shanghai"
        assert [
            event.event_type
            for event in session.scalars(select(RunEvent).order_by(RunEvent.sequence))
        ] == ["tool.started", "tool.completed"]
        audits = list(session.scalars(select(AuditEvent).order_by(AuditEvent.occurred_at)))
        assert [event.action for event in audits] == [
            "tool.invoke.started", "tool.invoke.succeeded"
        ]
    finally:
        session.close()


def test_failed_compensation_event_still_closes_invocation(runtime, monkeypatch):
    session, gateway = make_gateway(runtime)
    real_commit = session.commit
    commit_count = 0

    def fail_completion_and_compensation_event():
        nonlocal commit_count
        commit_count += 1
        if commit_count in {2, 3}:
            raise IntegrityError("commit", {}, Exception("event conflict"))
        real_commit()

    monkeypatch.setattr(session, "commit", fail_completion_and_compensation_event)
    try:
        with pytest.raises(ToolRuntimeError) as caught:
            execute(gateway)
        assert caught.value.code == "tool_execution_failed"
        invocation = session.scalar(select(ToolInvocation))
        assert invocation.status == "completed"
        assert invocation.error_code is None
        assert invocation.result_summary["timezone"] == "Asia/Shanghai"
    finally:
        session.close()

def test_completion_uses_preserved_invocation_id_after_rollback(
    runtime, monkeypatch
):
    session, gateway = make_gateway(runtime)
    real_commit = session.commit
    real_rollback = gateway._rollback_safely
    captured = {}
    commit_count = 0

    original_finish = gateway._commit_finished
    original_compensation = gateway._compensate_failed_completion
    compensation_ids = []

    def capture_invocation(invocation, *args, **kwargs):
        captured["invocation"] = invocation
        captured["invocation_id"] = str(invocation.id)
        return original_finish(invocation, *args, **kwargs)

    def capture_compensation(invocation_id, *args, **kwargs):
        compensation_ids.append(invocation_id)
        return original_compensation(invocation_id, *args, **kwargs)

    def fail_completion_once():
        nonlocal commit_count
        commit_count += 1
        if commit_count == 2:
            raise IntegrityError("commit", {}, Exception("event conflict"))
        real_commit()

    def rollback_and_detach():
        real_rollback()
        invocation = captured.get("invocation")
        if invocation is not None and invocation in session:
            session.expunge(invocation)

    monkeypatch.setattr(gateway, "_commit_finished", capture_invocation)
    monkeypatch.setattr(
        gateway, "_compensate_failed_completion", capture_compensation
    )
    monkeypatch.setattr(gateway, "_rollback_safely", rollback_and_detach)
    monkeypatch.setattr(session, "commit", fail_completion_once)
    try:
        result = execute(gateway)
        assert compensation_ids == [captured["invocation_id"]]
        assert result.invocation_id == captured["invocation_id"]
        invocation = session.scalar(select(ToolInvocation))
        assert invocation.status == "completed"
        assert invocation.error_code is None
    finally:
        session.close()
