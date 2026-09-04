from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import event, func, literal, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value


class DeadlineCommitExpired(Exception):
    pass


def commit_status_transition_before_deadline(
    session: Session,
    *,
    record: Any,
    expected_status: str,
    target_status: str,
    deadline: datetime,
    clock: Callable[[], datetime],
) -> None:
    record_type = type(record)
    record_id = record.id

    def transition_at_commit(committing_session: Session) -> None:
        dialect_name = committing_session.get_bind(
            mapper=record_type
        ).dialect.name
        before_deadline = (
            func.clock_timestamp() < deadline
            if dialect_name == "postgresql"
            else literal(clock() < deadline)
        )
        result = committing_session.execute(
            update(record_type)
            .where(
                record_type.id == record_id,
                record_type.status == expected_status,
                before_deadline,
            )
            .values(status=target_status)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise DeadlineCommitExpired
        set_committed_value(record, "status", target_status)

    event.listen(session, "before_commit", transition_at_commit)
    try:
        session.commit()
    finally:
        event.remove(session, "before_commit", transition_at_commit)
