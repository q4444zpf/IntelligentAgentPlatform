from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.db.base import Base


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value


def event(event_id, *, minute=0, unit="u1", project="p1", user="alice", **values):
    defaults = dict(
        id=event_id, unit_id=unit, project_id=project, user_id=user,
        actor_roles_json=["user"], authorization_scope="project",
        event_scope="project", auth_method=None,
        category="runtime", source="agent", action="agent.run.completed",
        status="succeeded", risk_level="low", trace_id="shared",
        run_id=f"run-{event_id}", resource_type="agent", resource_id=f"res-{event_id}",
        resource_name=f"name-{event_id}", summary="safe", metadata_json={"safe": True},
        idempotency_key=f"key-{event_id}", duration_ms=10,
        occurred_at=datetime(2026, 8, 3, 8, minute, tzinfo=timezone.utc),
    )
    defaults.update(values)
    return AuditEvent(**defaults)


def test_repository_applies_scope_filters_to_list_detail_and_related(session):
    from app.audit.policy import audit_scope_filters
    from app.audit.repository import AuditRepository
    from app.core.request_context import RequestContext

    session.add_all([
        event("mine", minute=1), event("other-user", minute=2, user="bob"),
        event("other-project", minute=3, project="p2"), event("other-unit", minute=4, unit="u2"),
    ])
    session.commit()
    repository = AuditRepository(session)
    cases = [
        (RequestContext(unit_id="u1", project_id="p1", user_id="alice"), ["mine"]),
        (RequestContext(unit_id="u1", project_id="p1", user_id="alice", roles=frozenset({"user", "project_admin"})), ["other-user", "mine"]),
        (RequestContext(unit_id="u1", project_id="p1", user_id="alice", roles=frozenset({"user", "unit_auditor"})), ["other-project", "other-user", "mine"]),
    ]
    for context, expected in cases:
        scope = audit_scope_filters(context)
        result = repository.list_events(scope, page=1, page_size=20)
        assert [item.id for item in result.items] == expected
        assert repository.get_event("other-unit", scope) is None
        assert [item.id for item in repository.list_related("mine", scope)] == list(reversed(expected))


def test_repository_filters_searches_literal_wildcards_and_summarizes_full_set(session):
    from app.audit.repository import AuditRepository

    session.add_all([
        event("a", minute=1, resource_name=r"literal %_\\ target", status="failed", risk_level="critical", source="tool"),
        event("b", minute=2, category="management", source="system", action="resource.updated", duration_ms=None),
        event("c", minute=2, source="llm", risk_level="high"),
        event("d", minute=0, category="management", source="system", risk_level="critical"),
        event("e", minute=0, category="security", source="auth", action="auth.login.succeeded"),
    ])
    session.commit()
    repository = AuditRepository(session)
    scope = [AuditEvent.unit_id == "u1", AuditEvent.project_id == "p1"]
    page = repository.list_events(scope, page=1, page_size=1)
    assert [item.id for item in page.items] == ["c"]
    assert page.total == 5
    assert page.summary == {"total": 5, "failed": 1, "high_risk": 3, "runtime": 2,
                            "management": 2, "by_source": {"auth": 1, "llm": 1, "system": 2, "tool": 1}}
    assert [item.id for item in repository.list_events(scope, page=1, page_size=20, query=r"%_\\").items] == ["a"]
    assert repository.list_events(scope, page=1, page_size=20, query="safe").items == []


def test_related_without_trace_returns_only_scoped_anchor(session):
    from app.audit.repository import AuditRepository

    session.add_all([event("anchor", trace_id=None), event("unrelated", minute=1, trace_id=None)])
    session.commit()
    scope = [AuditEvent.unit_id == "u1", AuditEvent.project_id == "p1", AuditEvent.user_id == "alice"]
    assert [item.id for item in AuditRepository(session).list_related("anchor", scope)] == ["anchor"]
