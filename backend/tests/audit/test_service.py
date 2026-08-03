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


def test_service_uses_same_scope_and_hides_missing_or_unauthorized():
    from app.audit.service import AuditEventNotFound, AuditService

    repository = CapturingRepository(None)
    context = RequestContext(unit_id="u1", project_id="p1", user_id="alice")
    with pytest.raises(AuditEventNotFound):
        AuditService(repository).get_event(context, "hidden")
    with pytest.raises(AuditEventNotFound):
        AuditService(repository).list_related(context, "hidden")
