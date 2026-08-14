from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.base import Base


class RuntimeCheckpoint(Base):
    __tablename__ = "runtime_checkpoints"
    __table_args__ = (UniqueConstraint("run_id", "checkpoint_key", name="uq_runtime_checkpoint_run_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    checkpoint_key: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    snapshot_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class RuntimeRunnerRequest(Base):
    __tablename__ = "runtime_runner_requests"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "action",
            "idempotency_key",
            name="uq_runtime_runner_request_run_action_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


@dataclass(frozen=True)
class StoredCheckpoint:
    checkpoint_key: str
    state: dict[str, Any]
    snapshot_digest: str | None
    idempotency_key: str | None


def _configured_max_bytes() -> int:
    raw = os.getenv("IAP_RUNNER_CHECKPOINT_MAX_BYTES", "2097152")
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError("IAP_RUNNER_CHECKPOINT_MAX_BYTES must be positive") from error
    if value <= 0:
        raise ValueError("IAP_RUNNER_CHECKPOINT_MAX_BYTES must be positive")
    return value


class CheckpointStore:
    def __init__(self, session: Session, *, max_bytes: int | None = None):
        self.session = session
        self.max_bytes = _configured_max_bytes() if max_bytes is None else max_bytes
        if self.max_bytes <= 0:
            raise ValueError("checkpoint max bytes must be positive")

    def save(
        self,
        run_id: str,
        checkpoint_key: str,
        state: dict[str, Any],
        snapshot_digest: str | None = None,
        idempotency_key: str | None = None,
        *,
        commit: bool = True,
    ) -> StoredCheckpoint:
        try:
            encoded = json.dumps(
                state,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ValueError("checkpoint state must be JSON serializable") from error
        if len(encoded) > self.max_bytes:
            raise ValueError("checkpoint state exceeds size limit")
        row = self.session.scalar(select(RuntimeCheckpoint).where(
            RuntimeCheckpoint.run_id == run_id,
            RuntimeCheckpoint.checkpoint_key == checkpoint_key,
        ))
        if row is None:
            row = RuntimeCheckpoint(
                run_id=run_id,
                checkpoint_key=checkpoint_key,
                state=state,
                snapshot_digest=snapshot_digest,
                idempotency_key=idempotency_key,
            )
            self.session.add(row)
        else:
            row.state = state
            row.snapshot_digest = snapshot_digest
            row.idempotency_key = idempotency_key
            row.updated_at = datetime.now(timezone.utc)
        if commit:
            self.session.commit()
        else:
            self.session.flush()
        return StoredCheckpoint(
            checkpoint_key=row.checkpoint_key,
            state=dict(row.state),
            snapshot_digest=row.snapshot_digest,
            idempotency_key=row.idempotency_key,
        )

    def load_latest(self, run_id: str) -> dict[str, Any] | None:
        checkpoint = self.load_latest_record(run_id)
        return checkpoint.state if checkpoint is not None else None

    def load_latest_record(self, run_id: str) -> StoredCheckpoint | None:
        row = self.session.scalar(select(RuntimeCheckpoint).where(
            RuntimeCheckpoint.run_id == run_id,
        ).order_by(RuntimeCheckpoint.updated_at.desc(), RuntimeCheckpoint.id.desc()))
        if row is None:
            return None
        return StoredCheckpoint(
            checkpoint_key=row.checkpoint_key,
            state=dict(row.state),
            snapshot_digest=row.snapshot_digest,
            idempotency_key=row.idempotency_key,
        )


class RunnerRequestStore:
    def __init__(self, session: Session):
        self.session = session

    def get(
        self, run_id: str, action: str, idempotency_key: str
    ) -> RuntimeRunnerRequest | None:
        return self.session.scalar(
            select(RuntimeRunnerRequest).where(
                RuntimeRunnerRequest.run_id == run_id,
                RuntimeRunnerRequest.action == action,
                RuntimeRunnerRequest.idempotency_key == idempotency_key,
            )
        )

    def count(self, run_id: str, action: str) -> int:
        from sqlalchemy import func

        return int(
            self.session.scalar(
                select(func.count(RuntimeRunnerRequest.id)).where(
                    RuntimeRunnerRequest.run_id == run_id,
                    RuntimeRunnerRequest.action == action,
                )
            )
            or 0
        )

    def add(
        self,
        *,
        run_id: str,
        action: str,
        idempotency_key: str,
        request_digest: str,
        response_json: dict[str, Any],
    ) -> None:
        self.session.add(
            RuntimeRunnerRequest(
                run_id=run_id,
                action=action,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                response_json=response_json,
            )
        )
        self.session.flush()
