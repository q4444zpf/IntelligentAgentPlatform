from datetime import datetime, timedelta, timezone
from typing import cast

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.conversations.models import AgentRun, Conversation, ToolInvocation
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

    lock_sql = str(session.statements[0].compile(dialect=postgresql.dialect()))
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
        conversation = Conversation(
            id="c1", project_id="p1", owner_id="u1", title="test"
        )
        message = Message(
            id="m1", conversation_id="c1", sequence=1, role="user", content="test"
        )
        run = AgentRun(
            id="r1",
            conversation_id="c1",
            trigger_message_id="m1",
            actor_type="agent",
            actor_id="a1",
            status="running",
        )
        session.add_all([conversation, message, run])
        repository = ConversationRepository(session)
        repository.add_tool_invocation(
            ToolInvocation(
                run_id="r1",
                tool_call_id="b",
                tool_id="system.get_current_time",
                tool_version="1.0.0",
                status="started",
                arguments_summary={},
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        repository.add_tool_invocation(
            ToolInvocation(
                run_id="r1",
                tool_call_id="a",
                tool_id="system.get_current_time",
                tool_version="1.0.0",
                status="started",
                arguments_summary={},
                created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            )
        )
        assert [
            item.tool_call_id for item in repository.list_tool_invocations("r1")
        ] == ["b", "a"]


class EventSequenceRecordingSession:
    def __init__(self):
        self.statements = []

    def scalar(self, statement):
        self.statements.append(statement)
        if len(self.statements) == 1:
            return AgentRun(
                id="r1",
                conversation_id="c1",
                trigger_message_id="m1",
                actor_type="agent",
                actor_id="a1",
                status="running",
            )
        return 4


def test_event_sequence_locks_run_before_allocating():
    session = EventSequenceRecordingSession()
    repository = ConversationRepository(cast(Session, session))

    sequence = repository.next_event_sequence("r1")

    lock_sql = str(session.statements[0].compile(dialect=postgresql.dialect()))
    assert "agent_runs" in lock_sql
    assert "FOR UPDATE" in lock_sql
    assert sequence == 5


def test_list_runs_returns_scoped_run(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.conversations.models import Message
    from app.db.base import Base

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'runs.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    with factory.begin() as session:
        session.add(
            Conversation(id="c1", project_id="p1", owner_id="u1", title="Flood")
        )
        session.add(
            Message(
                id="m1",
                conversation_id="c1",
                sequence=1,
                role="user",
                content="Run model",
            )
        )
        session.add(
            AgentRun(
                id="r1",
                conversation_id="c1",
                trigger_message_id="m1",
                actor_type="agent",
                actor_id="a1",
                status="running",
            )
        )

    with factory() as session:
        result = ConversationRepository(session).list_runs(
            project_id="p1", owner_id="u1", page=1, page_size=20
        )

    assert result.total == 1
    assert [item["id"] for item in result.items] == ["r1"]


def test_list_runs_filters_paginates_and_summarizes_full_scope(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.conversations.models import Message
    from app.db.base import Base

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'audit.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    moment = datetime(2026, 2, 1, tzinfo=timezone.utc)

    def add_run(
        session,
        run_id,
        status,
        created_at,
        *,
        project="p1",
        owner="u1",
        actor="a1",
        title="Gate Dispatch",
        tools=0,
        content="trigger",
    ):
        conversation_id = f"c-{run_id}"
        message_id = f"m-{run_id}"
        session.add(
            Conversation(
                id=conversation_id, project_id=project, owner_id=owner, title=title
            )
        )
        session.add(
            Message(
                id=message_id,
                conversation_id=conversation_id,
                sequence=1,
                role="user",
                content=content,
            )
        )
        session.add(
            AgentRun(
                id=run_id,
                conversation_id=conversation_id,
                trigger_message_id=message_id,
                actor_type="agent",
                actor_id=actor,
                status=status,
                created_at=created_at,
                updated_at=created_at + timedelta(milliseconds=1250),
            )
        )
        for index in range(tools):
            session.add(
                ToolInvocation(
                    run_id=run_id,
                    tool_call_id=f"{run_id}-{index}",
                    tool_id="clock",
                    tool_version="1",
                    status="completed",
                    arguments_summary={},
                )
            )

    with factory.begin() as session:
        add_run(
            session,
            "run-b",
            "completed",
            moment,
            tools=2,
            content="  release\n\t flood   model  " + "x" * 220,
        )
        add_run(session, "run-a", "running", moment, tools=1)
        add_run(session, "run-old", "failed", moment - timedelta(days=2), actor="a2")
        add_run(
            session,
            "hidden-project",
            "failed",
            moment + timedelta(days=1),
            project="p2",
            tools=4,
        )
        add_run(
            session,
            "hidden-owner",
            "failed",
            moment + timedelta(days=1),
            owner="u2",
            tools=4,
        )

    with factory() as session:
        repository = ConversationRepository(session)
        page = repository.list_runs(project_id="p1", owner_id="u1", page=1, page_size=2)
        filtered = repository.list_runs(
            project_id="p1",
            owner_id="u1",
            page=1,
            page_size=10,
            status="completed",
            actor_id="a1",
            query="dispatch",
            started_after=moment - timedelta(hours=1),
            started_before=moment + timedelta(hours=1),
        )
        by_id = repository.list_runs(
            project_id="p1", owner_id="u1", page=1, page_size=10, query="run-b"
        )
        second_page = repository.list_runs(
            project_id="p1", owner_id="u1", page=2, page_size=2
        )

    assert [item["id"] for item in page.items] == ["run-b", "run-a"]
    assert page.total == 3
    assert page.summary == {
        "total": 3,
        "completed": 1,
        "running": 1,
        "failed": 1,
        "tool_invocations": 3,
    }
    assert second_page.total == 3
    assert [item["id"] for item in second_page.items] == ["run-old"]
    assert [item["id"] for item in filtered.items] == ["run-b"]
    assert [item["id"] for item in by_id.items] == ["run-b"]
    item = page.items[0]
    assert item["conversation_title"] == "Gate Dispatch"
    assert item["trigger_message_id"] == "m-run-b"
    assert item["trigger_summary"].startswith("release flood model ")
    assert len(item["trigger_summary"]) == 200
    assert item["tool_invocation_count"] == 2
    assert item["duration_ms"] == 1250
