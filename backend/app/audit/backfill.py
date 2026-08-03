from datetime import timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.audit.recorder import AuditRecorder, AuditRecordRequest
from app.conversations.models import AgentRun, Conversation


_STATUS_MAP = {
    "queued": "started",
    "running": "started",
    "completed": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
}


def _audit_status(run_status: str) -> tuple[str, str | None]:
    status = _STATUS_MAP.get(run_status)
    if status is None:
        return "failed", "unknown_agent_run_status"
    return status, None


def backfill_agent_run_snapshots(
    session_factory: sessionmaker[Session],
    *,
    batch_size: int = 500,
) -> int:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")

    recorder = AuditRecorder()
    inserted = 0
    last_run_id: str | None = None
    while True:
        with session_factory() as session:
            statement = (
                select(AgentRun, Conversation)
                .join(Conversation, Conversation.id == AgentRun.conversation_id)
                .order_by(AgentRun.id)
                .limit(batch_size)
            )
            if last_run_id is not None:
                statement = statement.where(AgentRun.id > last_run_id)
            rows = session.execute(statement).all()
            if not rows:
                break

            for run, conversation in rows:
                key = f"audit-backfill:agent:{run.id}"
                status, error_code = _audit_status(run.status)
                occurred_at = run.created_at
                if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
                    occurred_at = occurred_at.replace(tzinfo=timezone.utc)
                result = recorder.record_with_result(
                    session,
                    AuditRecordRequest(
                        unit_id=conversation.unit_id,
                        project_id=conversation.project_id,
                        user_id=conversation.owner_id,
                        actor_role=run.actor_type,
                        category="runtime",
                        source="agent",
                        action="agent.run_snapshot",
                        status=status,
                        risk_level="low",
                        run_id=run.id,
                        resource_type="agent_run",
                        resource_id=run.id,
                        summary="",
                        metadata={"backfilled": True},
                        allowed_metadata_keys=frozenset({"backfilled"}),
                        idempotency_key=key,
                        occurred_at=occurred_at,
                        error_code=error_code,
                    ),
                )
                if result.inserted:
                    inserted += 1
            session.commit()
            last_run_id = rows[-1][0].id
    return inserted


def main() -> None:
    from app.core.database import SessionFactory

    count = backfill_agent_run_snapshots(SessionFactory)
    print(f"Backfilled {count} agent run snapshot(s).")


if __name__ == "__main__":
    main()
