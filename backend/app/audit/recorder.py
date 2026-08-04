from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.redaction import redact_metadata, redact_summary


_ENUMS = {
    "category": {"runtime", "management"},
    "source": {
        "agent", "tool", "mcp", "knowledge", "sandbox", "llm", "system",
    },
    "status": {"started", "succeeded", "failed", "cancelled"},
    "risk_level": {"low", "medium", "high", "critical"},
}
_LENGTHS = {
    "unit_id": 64,
    "project_id": 64,
    "user_id": 64,
    "actor_role": 40,
    "category": 30,
    "source": 30,
    "action": 100,
    "status": 30,
    "risk_level": 20,
    "trace_id": 64,
    "run_id": 36,
    "parent_event_id": 36,
    "resource_type": 50,
    "resource_id": 128,
    "resource_name": 200,
    "error_code": 120,
    "idempotency_key": 180,
}
_ACTOR_ROLES = frozenset({"user", "project_admin", "unit_auditor"})


def normalize_actor_role(value: str | None) -> str:
    if value is None or value == "unknown":
        return "unknown"
    roles = value.split(",")
    if any(not role or role.strip() != role for role in roles):
        raise ValueError("invalid actor_role")
    role_set = frozenset(roles)
    if len(role_set) != len(roles) or not role_set <= _ACTOR_ROLES:
        raise ValueError("invalid actor_role")
    normalized = ",".join(sorted(role_set))
    if value != normalized:
        raise ValueError("actor_role must use deterministic sorted roles")
    return normalized


@dataclass(frozen=True)
class AuditRecordRequest:
    unit_id: str
    category: str
    source: str
    action: str
    status: str
    risk_level: str
    idempotency_key: str
    occurred_at: datetime
    project_id: str | None = None
    user_id: str | None = None
    actor_role: str | None = None
    trace_id: str | None = None
    run_id: str | None = None
    parent_event_id: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    resource_name: str | None = None
    summary: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    duration_ms: int | None = None
    allowed_metadata_keys: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class AuditRecordResult:
    event: AuditEvent
    inserted: bool


def _validate(request: AuditRecordRequest) -> None:
    normalize_actor_role(request.actor_role)
    required_strings = (
        "unit_id", "category", "source", "action", "status", "risk_level",
        "idempotency_key",
    )
    for field_name in required_strings:
        value = getattr(request, field_name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field_name} must be a non-empty string")
    if not isinstance(request.occurred_at, datetime):
        raise ValueError("occurred_at must be a timezone-aware datetime")
    for field_name in _LENGTHS:
        value = getattr(request, field_name)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{field_name} must be a string")
    if not isinstance(request.summary, str):
        raise ValueError("summary must be a string")
    if not isinstance(request.metadata, Mapping):
        raise ValueError("metadata must be a mapping")
    if isinstance(request.allowed_metadata_keys, (str, bytes)) or not isinstance(
        request.allowed_metadata_keys, Collection
    ):
        raise ValueError("allowed_metadata_keys must be a collection of strings")
    if not all(isinstance(key, str) for key in request.allowed_metadata_keys):
        raise ValueError("allowed_metadata_keys must contain only strings")
    for field_name, allowed in _ENUMS.items():
        if getattr(request, field_name) not in allowed:
            raise ValueError(f"invalid {field_name}")
    for field_name, max_length in _LENGTHS.items():
        value = getattr(request, field_name)
        if value is not None and len(value) > max_length:
            raise ValueError(f"{field_name} exceeds {max_length} characters")
    if request.occurred_at.tzinfo is None or request.occurred_at.utcoffset() is None:
        raise ValueError("occurred_at must include timezone information")
    if request.duration_ms is not None and (
        isinstance(request.duration_ms, bool)
        or not isinstance(request.duration_ms, int)
        or request.duration_ms < 0
    ):
        raise ValueError("duration_ms must be a non-negative integer")


def _is_idempotency_conflict(error: IntegrityError) -> bool:
    original = error.orig
    diagnostic = getattr(original, "diag", None)
    if getattr(diagnostic, "constraint_name", None) == "uq_audit_idempotency_key":
        return True
    message = str(original).casefold()
    return (
        "uq_audit_idempotency_key" in message
        or "unique constraint failed: audit_events.idempotency_key" in message
    )


class AuditRecorder:
    def record(self, session: Session, request: AuditRecordRequest) -> AuditEvent:
        return self.record_with_result(session, request).event

    def record_with_result(
        self,
        session: Session,
        request: AuditRecordRequest,
    ) -> AuditRecordResult:
        _validate(request)
        lookup = select(AuditEvent).where(
            AuditEvent.idempotency_key == request.idempotency_key
        )
        existing = session.scalar(lookup)
        if existing is not None:
            return AuditRecordResult(event=existing, inserted=False)

        event = AuditEvent(
            unit_id=request.unit_id,
            project_id=request.project_id,
            user_id=request.user_id,
            actor_role=normalize_actor_role(request.actor_role),
            category=request.category,
            source=request.source,
            action=request.action,
            status=request.status,
            risk_level=request.risk_level,
            trace_id=request.trace_id,
            run_id=request.run_id,
            parent_event_id=request.parent_event_id,
            resource_type=request.resource_type,
            resource_id=request.resource_id,
            resource_name=request.resource_name,
            summary=redact_summary(request.summary),
            metadata_json=redact_metadata(
                request.metadata, allowed_keys=request.allowed_metadata_keys,
            ),
            error_code=request.error_code,
            duration_ms=request.duration_ms,
            idempotency_key=request.idempotency_key,
            occurred_at=request.occurred_at,
        )
        try:
            with session.begin_nested():
                session.add(event)
                session.flush()
        except IntegrityError as error:
            if not _is_idempotency_conflict(error):
                raise
            existing = session.scalar(lookup)
            if existing is None:
                raise
            return AuditRecordResult(event=existing, inserted=False)
        return AuditRecordResult(event=event, inserted=True)
