from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from app.audit.recorder import AuditRecorder, AuditRecordRequest
from app.core.request_context import RequestContext


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
    stable_request_id = request_id or str(uuid4())
    with session_factory.begin() as session:
        recorder.record(session, AuditRecordRequest(
            unit_id=context.unit_id,
            project_id=context.project_id,
            user_id=context.user_id,
            actor_role=context.role,
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
            idempotency_key=(
                f"management:{stable_request_id}:{source}:{action}:{resource_id}"
            ),
            occurred_at=datetime.now(UTC),
        ))


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
                            request_id=request.headers.get("X-Request-ID"),
                        )
                    raise

            return handler

    return ManagementAuditRoute
