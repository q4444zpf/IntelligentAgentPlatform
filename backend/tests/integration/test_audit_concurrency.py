import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, delete, event, func, literal, select
from sqlalchemy.orm import Session, sessionmaker

from app.audit.backfill import backfill_agent_run_snapshots
from app.audit.models import AuditEvent
from app.audit.recorder import AuditRecorder, AuditRecordRequest
from app.conversations.models import AgentRun, Conversation, Message


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="requires PostgreSQL",
)


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if database_url is None:
        yield
        return
    env = os.environ | {"DATABASE_URL": database_url}
    subprocess.run(
        (
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "backend/alembic.ini",
            "upgrade",
            "head",
        ),
        check=True,
        env=env,
        timeout=60,
    )
    yield


def make_factory(database_url: str):
    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"options": "-c lock_timeout=3000 -c statement_timeout=5000"},
    )
    factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
        class_=Session,
    )
    return engine, factory


def make_request(*, key: str, run_id: str) -> AuditRecordRequest:
    return AuditRecordRequest(
        unit_id="integration-unit",
        project_id="integration-project",
        user_id="integration-user",
        actor_role="agent",
        category="runtime",
        source="agent",
        action="agent.run_snapshot",
        status="succeeded",
        risk_level="low",
        run_id=run_id,
        resource_type="agent_run",
        resource_id=run_id,
        metadata={"backfilled": True},
        allowed_metadata_keys=frozenset({"backfilled"}),
        idempotency_key=key,
        occurred_at=datetime.now(timezone.utc),
    )


def observe_audit_insert(engine, attempted: threading.Event) -> None:
    @event.listens_for(engine, "before_cursor_execute")
    def before_cursor_execute(
        _connection, _cursor, statement, _parameters, _context, _many
    ):
        if "insert into audit_events" in statement.casefold():
            attempted.set()


def test_record_with_result_reports_real_unique_key_loser_and_keeps_session_usable():
    database_url = os.environ["TEST_DATABASE_URL"]
    winner_engine, winner_factory = make_factory(database_url)
    loser_engine, loser_factory = make_factory(database_url)
    attempted = threading.Event()
    observe_audit_insert(loser_engine, attempted)
    suffix = uuid.uuid4().hex
    key = f"integration-recorder-race:{suffix}"
    request = make_request(key=key, run_id=str(uuid.uuid4()))
    result: dict[str, object] = {}
    thread: threading.Thread | None = None

    def run_loser() -> None:
        try:
            with loser_factory() as session:
                value = AuditRecorder().record_with_result(session, request)
                result["value"] = value
                result["session_probe"] = session.scalar(select(literal(1)))
                session.commit()
        except BaseException as error:
            result["error"] = error

    try:
        with winner_factory() as winner:
            winning = AuditRecorder().record_with_result(winner, request)
            assert winning.inserted is True
            thread = threading.Thread(target=run_loser)
            thread.start()
            assert attempted.wait(5), "loser did not attempt the concurrent insert"
            winner.commit()
            thread.join(5)
            assert not thread.is_alive(), "loser remained blocked after winner commit"

        assert "error" not in result
        losing = result["value"]
        assert losing.inserted is False
        assert losing.event.id == winning.event.id
        assert result["session_probe"] == 1
        with winner_factory() as verification:
            assert verification.scalar(
                select(func.count()).select_from(AuditEvent).where(
                    AuditEvent.idempotency_key == key
                )
            ) == 1
    finally:
        if thread is not None:
            thread.join(7)
        thread_stopped = thread is None or not thread.is_alive()
        if not thread_stopped:
            pytest.fail("loser thread did not stop within bounded database timeouts")
        with winner_factory.begin() as cleanup:
            cleanup.execute(delete(AuditEvent).where(AuditEvent.idempotency_key == key))
        winner_engine.dispose()
        loser_engine.dispose()


def test_backfill_returns_zero_when_another_transaction_wins_the_unique_key():
    database_url = os.environ["TEST_DATABASE_URL"]
    winner_engine, winner_factory = make_factory(database_url)
    loser_engine, loser_factory = make_factory(database_url)
    attempted = threading.Event()
    observe_audit_insert(loser_engine, attempted)
    suffix = uuid.uuid4().hex
    conversation_id = str(uuid.uuid4())
    message_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    key = f"audit-backfill:agent:{run_id}"
    request = make_request(key=key, run_id=run_id)
    result: dict[str, object] = {}
    thread: threading.Thread | None = None

    def run_losing_backfill() -> None:
        try:
            result["count"] = backfill_agent_run_snapshots(
                loser_factory,
                batch_size=1,
            )
        except BaseException as error:
            result["error"] = error

    try:
        with winner_factory.begin() as seed:
            seed.add(
                Conversation(
                    id=conversation_id,
                    unit_id="integration-unit",
                    project_id="integration-project",
                    owner_id="integration-user",
                    title="race fixture",
                )
            )
            seed.add(
                Message(
                    id=message_id,
                    conversation_id=conversation_id,
                    sequence=1,
                    role="user",
                    content="race fixture",
                )
            )
            seed.add(
                AgentRun(
                    id=run_id,
                    conversation_id=conversation_id,
                    trigger_message_id=message_id,
                    actor_type="agent",
                    actor_id="integration-agent",
                    status="completed",
                )
            )

        with winner_factory() as winner:
            winning = AuditRecorder().record_with_result(winner, request)
            assert winning.inserted is True
            thread = threading.Thread(target=run_losing_backfill)
            thread.start()
            assert attempted.wait(5), "backfill did not attempt the concurrent insert"
            winner.commit()
            thread.join(5)
            assert not thread.is_alive(), "backfill remained blocked after winner commit"

        assert "error" not in result
        assert result["count"] == 0
        with winner_factory() as verification:
            assert verification.scalar(
                select(func.count()).select_from(AuditEvent).where(
                    AuditEvent.idempotency_key == key
                )
            ) == 1
    finally:
        if thread is not None:
            thread.join(7)
        thread_stopped = thread is None or not thread.is_alive()
        if not thread_stopped:
            pytest.fail("backfill thread did not stop within bounded database timeouts")
        with winner_factory.begin() as cleanup:
            cleanup.execute(delete(AuditEvent).where(AuditEvent.idempotency_key == key))
            cleanup.execute(delete(AgentRun).where(AgentRun.id == run_id))
            cleanup.execute(delete(Message).where(Message.id == message_id))
            cleanup.execute(delete(Conversation).where(Conversation.id == conversation_id))
        winner_engine.dispose()
        loser_engine.dispose()
