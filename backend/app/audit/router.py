from collections.abc import Callable
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.request_context import RequestContext, require_request_context

from .repository import AuditRepository
from .schemas import AuditCategory, AuditEventDetail, AuditEventListItem, AuditEventPage, AuditRisk, AuditSource, AuditStatus
from .service import AuditEventNotFound, AuditService

ServiceFactory = Callable[[Session], AuditService]


def default_service_factory(session: Session) -> AuditService:
    return AuditService(AuditRepository(session))


def validate_dates(after: datetime | None, before: datetime | None) -> None:
    if any(value is not None and value.utcoffset() is None for value in (after, before)):
        raise HTTPException(status_code=422, detail="审计时间筛选必须包含时区")
    if after is not None and before is not None and after > before:
        raise HTTPException(status_code=422, detail="开始时间不得晚于结束时间")


def create_router(service_factory: ServiceFactory = default_service_factory) -> APIRouter:
    router = APIRouter()

    def service(session: Session = Depends(get_session)) -> AuditService:
        return service_factory(session)

    @router.get("/events", response_model=AuditEventPage)
    def list_events(
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        category: AuditCategory | None = None,
        source: AuditSource | None = None,
        action: Annotated[str | None, Query(max_length=100)] = None,
        status: AuditStatus | None = None,
        risk_level: AuditRisk | None = None,
        project_id: Annotated[str | None, Query(max_length=64)] = None,
        user_id: Annotated[str | None, Query(max_length=64)] = None,
        query: Annotated[str | None, Query(max_length=200)] = None,
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
        context: RequestContext = Depends(require_request_context),
        manager: AuditService = Depends(service),
    ):
        validate_dates(occurred_after, occurred_before)
        return manager.list_events(
            context, page=page, page_size=page_size, category=category, source=source,
            action=action, status=status, risk_level=risk_level, project_id=project_id,
            user_id=user_id, query=query, occurred_after=occurred_after, occurred_before=occurred_before,
        )

    def safely(operation):
        try:
            return operation()
        except AuditEventNotFound as error:
            raise HTTPException(status_code=404, detail="记录不存在或无权访问") from error

    @router.get("/events/{event_id}", response_model=AuditEventDetail)
    def get_event(event_id: str, context: RequestContext = Depends(require_request_context), manager: AuditService = Depends(service)):
        return safely(lambda: manager.get_event(context, event_id))

    @router.get("/events/{event_id}/related", response_model=list[AuditEventListItem])
    def list_related(event_id: str, context: RequestContext = Depends(require_request_context), manager: AuditService = Depends(service)):
        return safely(lambda: manager.list_related(context, event_id))

    return router


router = create_router()
