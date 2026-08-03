from fastapi.testclient import TestClient

from app.conversations.router import default_run_dispatcher
from app.main import app


def test_application_mounts_audit_router():
    paths = set(app.openapi()["paths"])
    assert "/api/audit/events" in paths
    assert "/api/audit/events/{event_id}" in paths
    assert "/api/audit/events/{event_id}/related" in paths


def test_application_shutdown_closes_run_dispatcher(monkeypatch):
    calls: list[tuple[bool, bool]] = []

    def shutdown(*, wait: bool, cancel_futures: bool) -> None:
        calls.append((wait, cancel_futures))

    monkeypatch.setattr(default_run_dispatcher, "shutdown", shutdown)

    with TestClient(app):
        pass

    assert calls == [(False, True)]
