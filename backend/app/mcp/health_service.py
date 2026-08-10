from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.db.platform_models import McpClientRecord, McpHealthCheckRecord, McpOperationRecord, RegisteredToolRecord


class McpHealthService:
    def __init__(self, session_factory: sessionmaker[Session], *, lease_seconds: int = 60, failure_threshold: int = 3, interval_seconds: int = 300):
        self.session_factory = session_factory
        self.lease_seconds = lease_seconds
        self.failure_threshold = failure_threshold
        self.interval_seconds = interval_seconds

    def is_due(self, session: Session, client_key: str, *, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        row = session.get(McpClientRecord, client_key)
        return row is not None and (row.last_checked_at is None or now - row.last_checked_at >= timedelta(seconds=self.interval_seconds))

    def acquire_lease(self, session: Session, client_key: str, *, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        until = now + timedelta(seconds=self.lease_seconds)
        result = session.execute(
            update(McpClientRecord)
            .where(McpClientRecord.client_key == client_key, or_(McpClientRecord.health_lease_until.is_(None), McpClientRecord.health_lease_until <= now))
            .values(health_lease_until=until)
        )
        session.flush()
        return result.rowcount == 1

    def release_lease(self, session: Session, client_key: str, *, now: datetime | None = None) -> bool:
        result = session.execute(update(McpClientRecord).where(McpClientRecord.client_key == client_key).values(health_lease_until=None))
        session.flush()
        return result.rowcount == 1

    def record_result(self, session: Session, client_key: str, *, ok: bool, phase: str, latency_ms: int | None = None, error_code: str | None = None, error_message: str | None = None, now: datetime | None = None) -> McpClientRecord:
        now = now or datetime.now(UTC)
        row = session.get(McpClientRecord, client_key)
        if row is None:
            raise ValueError("MCP client not found")
        row.last_checked_at = now
        row.last_latency_ms = latency_ms
        row.health_lease_until = None
        if ok:
            row.health_status = "healthy"
            row.last_success_at = now
            row.failure_count = 0
            row.last_error_code = None
            row.last_error_message = None
            status = "healthy"
        else:
            row.failure_count += 1
            row.last_error_code = error_code
            row.last_error_message = error_message[:500] if error_message else None
            if row.failure_count >= self.failure_threshold:
                row.health_status = "offline"
            else:
                row.health_status = "degraded"
            status = row.health_status
        session.add(McpHealthCheckRecord(id=str(uuid4()), client_id=row.client_id or client_key, status=status, phase=phase, latency_ms=latency_ms, error_code=error_code, error_message=(error_message[:500] if error_message else None), checked_at=now))
        session.flush()
        return row

    def start_operation(self, session: Session, client_key: str, operation_type: str) -> McpOperationRecord:
        row = McpOperationRecord(id=str(uuid4()), client_id=client_key, operation_type=operation_type, status="queued", phase="queued")
        session.add(row)
        session.flush()
        return row

    def update_operation(self, session: Session, operation_id: str, *, status: str, phase: str, result: dict[str, Any] | None = None, error_code: str | None = None, error_message: str | None = None) -> McpOperationRecord:
        row = session.get(McpOperationRecord, operation_id)
        if row is None:
            raise ValueError("MCP operation not found")
        row.status = status
        row.phase = phase
        row.result = result
        row.error_code = error_code
        row.error_message = error_message[:500] if error_message else None
        if status in {"succeeded", "failed"}:
            row.completed_at = datetime.now(UTC)
        session.flush()
        return row

    def mark_source_unavailable(self, session: Session, client_key: str) -> None:
        rows = session.scalars(select(RegisteredToolRecord).where(RegisteredToolRecord.source == "mcp", RegisteredToolRecord.source_resource_id == client_key))
        for row in rows:
            row.source_available = False
            row.published = False
        session.flush()
