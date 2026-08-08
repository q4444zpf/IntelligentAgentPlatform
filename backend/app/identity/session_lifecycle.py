from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuthSession


def revoke_user_sessions(
    session: Session,
    user_id: str,
    reason: str,
    *,
    now: datetime | None = None,
) -> None:
    revoked_at = now or datetime.now(timezone.utc)
    for auth in session.scalars(
        select(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
    ):
        auth.revoked_at = revoked_at
        auth.revoke_reason = reason
