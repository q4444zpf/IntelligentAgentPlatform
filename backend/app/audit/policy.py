from sqlalchemy import and_, false, or_
from sqlalchemy.sql.elements import ColumnElement

from app.core.request_context import RequestContext
from app.identity.schemas import AuthorizationContext

from .models import AuditEvent


def audit_scope_predicate(context: AuthorizationContext) -> ColumnElement[bool]:
    terms: list[ColumnElement[bool]] = []
    for grant in context.grants:
        if grant.permission_code != "audit.read":
            continue
        if grant.data_scope == "unit":
            terms.append(AuditEvent.unit_id == context.unit_id)
            continue
        if not grant.project_ids:
            continue
        project_term = AuditEvent.project_id.in_(grant.project_ids)
        if grant.data_scope == "own":
            project_term = and_(project_term, AuditEvent.user_id == context.user_id)
        terms.append(and_(AuditEvent.unit_id == context.unit_id, project_term))
    return or_(*terms) if terms else false()


def audit_scope_filters(context: RequestContext | AuthorizationContext) -> list[ColumnElement[bool]]:
    if isinstance(context, AuthorizationContext):
        return [audit_scope_predicate(context)]
    filters = [AuditEvent.unit_id == context.unit_id]
    if "unit_auditor" in context.role_codes:
        return filters
    filters.append(AuditEvent.project_id == context.project_id)
    if "project_admin" not in context.role_codes:
        filters.append(AuditEvent.user_id == context.user_id)
    return filters
