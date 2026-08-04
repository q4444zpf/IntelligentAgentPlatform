from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from .models import AuditEvent

_AUDIT_SOURCES = ("agent", "tool", "mcp", "knowledge", "sandbox", "llm", "system")



@dataclass(frozen=True)
class AuditListResult:
    items: list[AuditEvent]
    page: int
    page_size: int
    total: int
    summary: dict


class AuditRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_events(
        self, scope: Sequence[ColumnElement[bool]], *, page: int, page_size: int,
        category: str | None = None, source: str | None = None,
        action: str | None = None, status: str | None = None,
        risk_level: str | None = None, project_id: str | None = None,
        user_id: str | None = None, query: str | None = None,
        occurred_after: datetime | None = None, occurred_before: datetime | None = None,
    ) -> AuditListResult:
        filters = list(scope)
        for column, value in (
            (AuditEvent.category, category), (AuditEvent.source, source),
            (AuditEvent.action, action), (AuditEvent.status, status),
            (AuditEvent.risk_level, risk_level), (AuditEvent.project_id, project_id),
            (AuditEvent.user_id, user_id),
        ):
            if value is not None:
                filters.append(column == value)
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
            pattern = f"%{escaped}%"
            filters.append(or_(
                AuditEvent.id.ilike(pattern, escape="\\"),
                AuditEvent.trace_id.ilike(pattern, escape="\\"),
                AuditEvent.run_id.ilike(pattern, escape="\\"),
                AuditEvent.resource_id.ilike(pattern, escape="\\"),
                AuditEvent.resource_name.ilike(pattern, escape="\\"),
            ))
        if occurred_after is not None:
            filters.append(AuditEvent.occurred_at >= occurred_after)
        if occurred_before is not None:
            filters.append(AuditEvent.occurred_at <= occurred_before)

        scoped = select(AuditEvent).where(*filters).subquery()
        items = list(self.session.scalars(
            select(AuditEvent).join(scoped, AuditEvent.id == scoped.c.id)
            .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        ))
        aggregate = self.session.execute(select(
            func.count(scoped.c.id).label("total"),
            func.coalesce(func.sum(case((scoped.c.status == "failed", 1), else_=0)), 0).label("failed"),
            func.coalesce(
                func.sum(case((scoped.c.risk_level.in_(("high", "critical")), 1), else_=0)),
                0,
            ).label("high_risk"),
            func.coalesce(func.sum(case((scoped.c.category == "runtime", 1), else_=0)), 0).label("runtime"),
            func.coalesce(func.sum(case((scoped.c.category == "management", 1), else_=0)), 0).label("management"),
            *(
                func.coalesce(
                    func.sum(case((scoped.c.source == source_name, 1), else_=0)), 0
                ).label(f"source_{source_name}")
                for source_name in _AUDIT_SOURCES
            ),
        )).mappings().one()
        summary = {key: int(aggregate[key]) for key in ("total", "failed", "high_risk", "runtime", "management")}
        summary["by_source"] = {
            source_name: int(aggregate[f"source_{source_name}"])
            for source_name in _AUDIT_SOURCES
            if aggregate[f"source_{source_name}"]
        }
        return AuditListResult(items, page, page_size, summary["total"], summary)

    def get_event(self, event_id: str, scope: Sequence[ColumnElement[bool]]) -> AuditEvent | None:
        return self.session.scalar(select(AuditEvent).where(AuditEvent.id == event_id, *scope))

    def list_related(self, event_id: str, scope: Sequence[ColumnElement[bool]]) -> list[AuditEvent]:
        anchor = self.get_event(event_id, scope)
        if anchor is None:
            return []
        if anchor.trace_id is None:
            return [anchor]
        return list(self.session.scalars(
            select(AuditEvent).where(*scope, AuditEvent.trace_id == anchor.trace_id)
            .order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
        ))
