from app.core.request_context import RequestContext

from .policy import audit_scope_filters
from .repository import AuditRepository
from .schemas import AuditEventDetail, AuditEventListItem, AuditEventPage


class AuditEventNotFound(Exception):
    pass


class AuditService:
    def __init__(self, repository: AuditRepository):
        self.repository = repository

    def list_events(self, context: RequestContext, **filters) -> AuditEventPage:
        result = self.repository.list_events(audit_scope_filters(context), **filters)
        return AuditEventPage(
            items=[AuditEventListItem.model_validate(item) for item in result.items],
            page=result.page, page_size=result.page_size, total=result.total,
            summary=result.summary,
        )

    def get_event(self, context: RequestContext, event_id: str) -> AuditEventDetail:
        value = self.repository.get_event(event_id, audit_scope_filters(context))
        if value is None:
            raise AuditEventNotFound(event_id)
        return AuditEventDetail.model_validate(value)

    def list_related(self, context: RequestContext, event_id: str) -> list[AuditEventListItem]:
        scope = audit_scope_filters(context)
        related = self.repository.list_related(event_id, scope)
        if not related:
            raise AuditEventNotFound(event_id)
        return [AuditEventListItem.model_validate(item) for item in related]
