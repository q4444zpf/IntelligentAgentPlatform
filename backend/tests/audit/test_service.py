from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.request_context import RequestContext


class CapturingRepository:
    def __init__(self, value=None):
        self.value = value
        self.calls = []

    def list_events(self, scope, **filters):
        self.calls.append((scope, filters))
        return self.value

    def get_event(self, event_id, scope):
        self.calls.append((event_id, scope))
        return self.value

    def list_related(self, event_id, scope):
        self.calls.append((event_id, scope))
        return self.value or []


def test_service_builds_policy_once_and_forwards_authorized_filters():
    from app.audit.service import AuditService

    result = SimpleNamespace(items=[], page=1, page_size=20, total=0, summary={
        "total": 0, "failed": 0, "high_risk": 0, "runtime": 0, "management": 0, "by_source": {},
    })
    repository = CapturingRepository(result)
    context = RequestContext(unit_id="u1", project_id="p1", user_id="alice", roles=frozenset({"project_admin"}))
    page = AuditService(repository).list_events(context, page=1, page_size=20, project_id="p1", user_id="bob")
    assert page.total == 0
    assert repository.calls[0][1]["project_id"] == "p1"
    assert repository.calls[0][1]["user_id"] == "bob"
    assert len(repository.calls[0][0]) == 2


def test_service_serializes_stable_actor_role_and_scope_contract():
    from app.audit.service import AuditService

    item = SimpleNamespace(
        id="event-1", unit_id="u1", project_id=None, user_id="alice",
        actor_roles_json=["unit_admin", "user"], authorization_scope="unit",
        event_scope="unit", auth_method="oidc", category="security", source="auth",
        action="auth.login.succeeded", status="succeeded", risk_level="medium",
        trace_id=None, run_id=None, resource_type="user", resource_id="alice",
        resource_name=None, duration_ms=None,
        occurred_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )
    repository = CapturingRepository(SimpleNamespace(
        items=[item], page=1, page_size=20, total=1,
        summary={"total": 1, "failed": 0, "high_risk": 0, "runtime": 0,
                 "management": 0, "by_source": {"auth": 1}},
    ))

    page = AuditService(repository).list_events(
        RequestContext(unit_id="u1", project_id="p1", user_id="alice"),
        page=1,
        page_size=20,
    )

    assert page.items[0].actor_roles == ["unit_admin", "user"]
    assert page.items[0].event_scope == "unit"
    assert page.items[0].auth_method == "oidc"


def test_service_uses_same_scope_and_hides_missing_or_unauthorized():
    from app.audit.service import AuditEventNotFound, AuditService

    repository = CapturingRepository(None)
    context = RequestContext(unit_id="u1", project_id="p1", user_id="alice")
    with pytest.raises(AuditEventNotFound):
        AuditService(repository).get_event(context, "hidden")
    with pytest.raises(AuditEventNotFound):
        AuditService(repository).list_related(context, "hidden")
