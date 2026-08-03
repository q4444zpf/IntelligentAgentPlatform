from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
import re
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from app.audit.recorder import AuditRecorder, AuditRecordRequest
from app.core.request_context import RequestContext


MANAGEMENT_REQUEST_ID_STATE_KEY = "management_request_id"
_SAFE_CORRELATION = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ManagementAuditIdentity:
    event_id: str
    correlation_id: str

    def __str__(self) -> str:
        return self.event_id


def management_request_id(request: Request) -> ManagementAuditIdentity:
    existing = getattr(request.state, MANAGEMENT_REQUEST_ID_STATE_KEY, None)
    if existing:
        return existing
    supplied = request.headers.get("X-Request-ID", "")
    correlation_id = supplied if _SAFE_CORRELATION.fullmatch(supplied) else str(uuid4())
    identity = ManagementAuditIdentity(str(uuid4()), correlation_id)
    setattr(request.state, MANAGEMENT_REQUEST_ID_STATE_KEY, identity)
    return identity


def management_event_id(identity: str | ManagementAuditIdentity | None) -> str:
    return identity.event_id if isinstance(identity, ManagementAuditIdentity) else str(uuid4())


def management_trace_id(identity: str | ManagementAuditIdentity | None) -> str | None:
    if isinstance(identity, ManagementAuditIdentity):
        return identity.correlation_id
    if isinstance(identity, str) and _SAFE_CORRELATION.fullmatch(identity):
        return identity
    return None

def record_failed_management(
    session_factory: sessionmaker[Session],
    recorder: AuditRecorder,
    context: RequestContext,
    *,
    source: str,
    action: str,
    resource_type: str,
    resource_id: str,
    error_code: str,
    request_id: str | None,
    risk_level: str = "high",
) -> None:
    event_id = management_event_id(request_id)
    try:
        with session_factory.begin() as session:
            recorder.record(session, AuditRecordRequest(
                unit_id=context.unit_id,
                project_id=context.project_id,
                user_id=context.user_id,
                actor_role=context.role,
                trace_id=management_trace_id(request_id),
                category="management",
                source=source,
                action=action,
                status="failed",
                risk_level=risk_level,
                resource_type=resource_type,
                resource_id=resource_id,
                summary=f"{resource_type} {resource_id} management operation failed",
                metadata={},
                allowed_metadata_keys=frozenset(),
                error_code=error_code,
                idempotency_key=f"management:{event_id}:failed:{source}:{action}:{resource_id}",
                occurred_at=datetime.now(UTC),
            ))
    except Exception:
        _logger.warning(
            "management_failed_audit_write_failed",
            extra={"audit_source": source, "audit_action": action, "error_code": error_code},
        )

def management_audit_route_class(
    session_factory_provider: Callable[[], sessionmaker[Session]],
    recorder_provider: Callable[[], AuditRecorder],
    *,
    source: str,
    resource_type: str,
) -> type[APIRoute]:
    class ManagementAuditRoute(APIRoute):
        def get_route_handler(self):
            original = super().get_route_handler()

            async def handler(request: Request):
                try:
                    return await original(request)
                except RequestValidationError:
                    context = getattr(request.state, "management_context", None)
                    if context is not None and request.method != "GET":
                        identifiers = [
                            str(value)
                            for key, value in request.path_params.items()
                            if key.endswith("_id") or key.endswith("_key")
                        ]
                        resource_id = "/".join(identifiers) or resource_type
                        is_create = request.method == "POST" and (
                            not request.path_params or request.url.path.endswith("/copy")
                        )
                        action = (
                            "resource.deleted" if request.method == "DELETE"
                            else "resource.created" if is_create
                            else "resource.permission_changed"
                            if request.method == "PUT" and request.url.path.endswith("/tools")
                            else "resource.updated"
                        )
                        record_failed_management(
                            session_factory_provider(), recorder_provider(), context,
                            source=source, action=action, resource_type=resource_type,
                            resource_id=resource_id, error_code="REQUEST_VALIDATION",
                            request_id=management_request_id(request),
                        )
                    raise

            return handler

    return ManagementAuditRoute
