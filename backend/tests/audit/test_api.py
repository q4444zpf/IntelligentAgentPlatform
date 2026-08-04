from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.audit.models import AuditEvent
from app.db.base import Base

HEADERS = {"X-Unit-ID": "u1", "X-Project-ID": "p1", "X-User-ID": "alice"}


def build_client():
    from app.audit.repository import AuditRepository
    from app.audit.router import create_router
    from app.audit.service import AuditService

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(AuditEvent(
        id="visible", unit_id="u1", project_id="p1", user_id="alice",
        actor_roles_json=["user"], authorization_scope="project",
        event_scope="project", auth_method=None,
        category="runtime", source="agent", action="agent.run.completed", status="succeeded",
        risk_level="low", trace_id="trace", run_id="run-1", resource_type="agent",
        resource_id="agent-1", resource_name="Flood", summary="done", metadata_json={"safe": True},
        duration_ms=5, idempotency_key="visible", occurred_at=datetime(2026, 8, 3, tzinfo=timezone.utc)))
    session.commit()
    app = FastAPI()
    app.state.allow_dev_identity = True
    app.state.audit_session = session
    app.include_router(create_router(lambda _session: AuditService(AuditRepository(session))), prefix="/api/audit")
    return TestClient(app)


def test_api_lists_gets_and_relates_events():
    client = build_client()
    page = client.get("/api/audit/events", headers=HEADERS)
    detail = client.get("/api/audit/events/visible", headers=HEADERS)
    related = client.get("/api/audit/events/visible/related", headers=HEADERS)
    assert page.status_code == detail.status_code == related.status_code == 200
    assert page.json()["items"][0]["id"] == "visible"
    assert page.json()["items"][0]["actor_roles"] == ["user"]
    assert page.json()["items"][0]["event_scope"] == "project"
    assert detail.json()["metadata"] == {"safe": True}
    assert [item["id"] for item in related.json()] == ["visible"]


def test_unit_auditor_can_list_events_in_own_unit():
    client = build_client()
    response = client.get(
        "/api/audit/events",
        headers={**HEADERS, "X-User-Roles": "unit_auditor"},
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == ["visible"]


def test_api_validates_enums_dates_and_pagination():
    client = build_client()
    invalid = [
        {"category": "other"}, {"source": "api"}, {"status": "pending"}, {"risk_level": "urgent"},
        {"occurred_after": "2026-08-03T00:00:00"},
        {"occurred_after": "2026-08-04T00:00:00Z", "occurred_before": "2026-08-03T00:00:00Z"},
        {"page": 0}, {"page_size": 101},
    ]
    for params in invalid:
        assert client.get("/api/audit/events", params=params, headers=HEADERS).status_code == 422


def test_api_uses_same_safe_404_for_missing_and_unauthorized():
    client = build_client()
    expected = {"detail": "记录不存在或无权访问"}
    for event_id in ("missing", "visible"):
        headers = HEADERS if event_id == "missing" else {**HEADERS, "X-Unit-ID": "u2"}
        detail_response = client.get(f"/api/audit/events/{event_id}", headers=headers)
        related_response = client.get(f"/api/audit/events/{event_id}/related", headers=headers)
        assert detail_response.status_code == 404
        assert related_response.status_code == 404
        assert detail_response.json() == expected
        assert related_response.json() == expected


def test_api_detail_never_returns_sensitive_summary_fragments():
    from app.audit.recorder import AuditRecorder, AuditRecordRequest

    client = build_client()
    session = client.app.state.audit_session
    secret = "api-detail-secret-value"
    event = AuditRecorder().record(
        session,
        AuditRecordRequest(
            unit_id="u1", project_id="p1", user_id="alice", actor_roles=("user",),
            authorization_scope="project", event_scope="project",
            category="management", source="system", action="resource.updated",
            status="failed", risk_level="high", idempotency_key="sensitive-detail",
            occurred_at=datetime.now(timezone.utc),
            summary=f"api_key={secret}&safe=true",
        ),
    )
    session.commit()
    response = client.get(f"/api/audit/events/{event.id}", headers=HEADERS)
    assert response.json()["summary"] == "api_key=[REDACTED]&amp;safe=true"
