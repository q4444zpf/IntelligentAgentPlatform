from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.audit.backfill import backfill_agent_run_snapshots
from app.audit.models import AuditEvent
from app.conversations.models import AgentRun, Conversation, Message
from app.db.base import Base


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def seed_run(
    session: Session,
    *,
    run_id: str,
    unit_id: str,
    project_id: str,
    user_id: str,
    status: str,
    actor_roles: list[str] | None = None,
) -> None:
    conversation_id = f"conversation-{run_id}"
    message_id = f"message-{run_id}"
    session.add(
        Conversation(
            id=conversation_id,
            unit_id=unit_id,
            project_id=project_id,
            owner_id=user_id,
            title=f"prompt secret for {run_id}",
        )
    )
    session.add(
        Message(
            id=message_id,
            conversation_id=conversation_id,
            sequence=1,
            role="user",
            content=f"sensitive trigger input for {run_id}",
        )
    )
    session.add(
        AgentRun(
            id=run_id,
            conversation_id=conversation_id,
            trigger_message_id=message_id,
            actor_type="agent",
            actor_id=f"agent-{run_id}",
            actor_roles_json=actor_roles or [],
            status=status,
            created_at=datetime(2026, 8, 1, 8, 30, tzinfo=timezone.utc),
        )
    )


def test_backfills_terminal_runs_with_scope_status_and_safe_content(session_factory):
    with session_factory() as session:
        seed_run(
            session,
            run_id="run-completed",
            unit_id="unit-east",
            project_id="project-river",
            user_id="user-one",
            status="completed",
            actor_roles=["project_admin", "user"],
        )
        seed_run(
            session,
            run_id="run-failed",
            unit_id="unit-west",
            project_id="project-reservoir",
            user_id="user-two",
            status="failed",
        )
        session.commit()

    assert backfill_agent_run_snapshots(session_factory, batch_size=1) == 2

    with session_factory() as session:
        events = session.scalars(
            select(AuditEvent).order_by(AuditEvent.run_id)
        ).all()

    assert [event.run_id for event in events] == ["run-completed", "run-failed"]
    completed, failed = events
    assert (
        completed.unit_id,
        completed.project_id,
        completed.user_id,
        completed.status,
    ) == ("unit-east", "project-river", "user-one", "succeeded")
    assert (
        failed.unit_id,
        failed.project_id,
        failed.user_id,
        failed.status,
    ) == ("unit-west", "project-reservoir", "user-two", "failed")
    for event in events:
        assert event.action == "agent.run_snapshot"
        assert event.metadata_json == {"backfilled": True}
        assert event.summary == ""
        serialized = f"{event.summary} {event.metadata_json}"
        assert "prompt secret" not in serialized
        assert "sensitive trigger input" not in serialized
        assert event.idempotency_key == f"audit-backfill:agent:{event.run_id}"
    assert completed.actor_roles_json == ["project_admin", "user"]
    assert failed.actor_roles_json == []
    for event in events:
        assert event.authorization_scope == "project"
        assert event.event_scope == "project"
        assert event.auth_method is None


def test_backfill_is_idempotent_and_does_not_skip_runs_at_batch_boundaries(
    session_factory,
):
    with session_factory() as session:
        for run_id, status in (
            ("run-c", "completed"),
            ("run-a", "failed"),
            ("run-b", "completed"),
        ):
            seed_run(
                session,
                run_id=run_id,
                unit_id="unit-1",
                project_id="project-1",
                user_id="user-1",
                status=status,
            )
        session.commit()

    assert backfill_agent_run_snapshots(session_factory, batch_size=2) == 3
    assert backfill_agent_run_snapshots(session_factory, batch_size=2) == 0

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 3
        assert session.scalars(
            select(AuditEvent.run_id).order_by(AuditEvent.run_id)
        ).all() == ["run-a", "run-b", "run-c"]


@pytest.mark.parametrize(
    ("run_status", "audit_status", "error_code"),
    [
        ("queued", "started", None),
        ("running", "started", None),
        ("completed", "succeeded", None),
        ("failed", "failed", None),
        ("cancelled", "cancelled", None),
        ("corrupted-status", "failed", "unknown_agent_run_status"),
    ],
)
def test_backfill_snapshots_every_run_with_a_safe_status_mapping(
    session_factory, run_status, audit_status, error_code
):
    with session_factory() as session:
        seed_run(
            session,
            run_id="run-status",
            unit_id="unit-1",
            project_id="project-1",
            user_id="user-1",
            status=run_status,
        )
        session.commit()

    assert backfill_agent_run_snapshots(session_factory) == 1

    with session_factory() as session:
        event = session.scalar(select(AuditEvent))
        assert event is not None
        assert event.status == audit_status
        assert event.error_code == error_code
        assert event.metadata_json == {"backfilled": True}


def test_backfill_empty_database_returns_zero(session_factory):
    assert backfill_agent_run_snapshots(session_factory) == 0


@pytest.mark.parametrize("batch_size", [0, -1, True, 1.5])
def test_backfill_rejects_invalid_batch_size(session_factory, batch_size):
    with pytest.raises(ValueError, match="batch_size must be a positive integer"):
        backfill_agent_run_snapshots(session_factory, batch_size=batch_size)
