from sqlalchemy.sql.elements import ColumnElement

from app.core.request_context import RequestContext

from .models import AuditEvent


def audit_scope_filters(context: RequestContext) -> list[ColumnElement[bool]]:
    filters = [AuditEvent.unit_id == context.unit_id]
    if "unit_auditor" in context.roles:
        return filters
    filters.append(AuditEvent.project_id == context.project_id)
    if "project_admin" not in context.roles:
        filters.append(AuditEvent.user_id == context.user_id)
    return filters
