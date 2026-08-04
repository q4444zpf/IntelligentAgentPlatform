from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.conversations.models import (
    AgentRun,
    Conversation,
    Message,
    RunEvent,
    ToolInvocation,
)
from app.db.base import Base


def test_conversation_requires_unit_and_has_scope_index():
    table = Base.metadata.tables["conversations"]

    assert table.c.unit_id.nullable is False
    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in table.indexes
    }
    assert indexes["ix_conversations_unit_project_owner"] == (
        "unit_id",
        "project_id",
        "owner_id",
    )
    assert not any(columns == ("unit_id",) for columns in indexes.values())


def test_persists_project_scoped_conversation_graph():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        conversation = Conversation(unit_id="unit-1", project_id="p1", owner_id="u1", title="洪水研判")
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
        assert session.scalar(select(Conversation)).project_id == "p1"
        assert session.scalar(select(AgentRun)).actor_roles_json == [
            "project_admin",
            "user",
        ]
        assert session.scalar(select(RunEvent)).sequence == 1


def test_persists_tool_invocation_for_agent_run():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        conversation = Conversation(unit_id="unit-1", project_id="p1", owner_id="u1", title="查询时间")
        session.add(conversation)
        session.flush()
        message = Message(
            conversation_id=conversation.id,
            role="user",
            content="现在几点？",
        )
        session.add(message)
        session.flush()
        run = AgentRun(
            conversation_id=conversation.id,
            trigger_message_id=message.id,
            actor_type="agent",
            actor_id="default",
            actor_roles_json=[],
            status="running",
        )
        session.add(run)
        session.flush()
        session.add(
            ToolInvocation(
                run_id=run.id,
                tool_call_id="call-time-1",
                tool_id="system.get_current_time",
                tool_version="1.0.0",
                status="started",
                arguments_summary={"timezone": "Asia/Shanghai"},
                result_summary={"started": True},
                error_code="tool_execution_failed",
            )
        )
        session.commit()

        invocation = session.scalar(select(ToolInvocation))
        assert invocation is not None
        assert invocation.tool_id == "system.get_current_time"
        assert invocation.status == "started"
        assert invocation.error_code == "tool_execution_failed"


def test_agent_run_uses_json_role_snapshot_without_singular_compatibility_column():
    table = Base.metadata.tables["agent_runs"]

    assert "actor_roles_json" in table.c
    assert table.c.actor_roles_json.nullable is False
    assert table.c.actor_roles_json.default is not None
    assert "actor_role" not in table.c
