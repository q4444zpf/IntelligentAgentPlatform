from dataclasses import replace
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.audit.models import AuditEvent
from app.core.database import get_session
from app.db.base import Base
from app.identity.auth_router import SESSION_COOKIE, _hash
from app.identity.models import AuthSession, Project, Unit, UnitMembership, User
from app.main import app


def build_client(monkeypatch):
    import app.identity.auth_router as auth_router

    monkeypatch.setattr(
        auth_router,
        "settings",
        replace(auth_router.settings, session_cookie_secure=False),
    )
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        unit = Unit(id="unit-1", code="u1", name="Unit 1", status="active")
        project = Project(
            id="project-1", unit_id=unit.id, code="p1", name="Project 1", status="active"
        )
        alice = User(
            id="alice", display_name="Alice", email="alice@example.com",
            status="active", authorization_version=1,
        )
        bob = User(
            id="bob", display_name="Bob", email="bob@example.com",
            status="active", authorization_version=1,
        )
        session.add_all([unit, project, alice, bob])
        session.add_all([
            UnitMembership(id="membership-alice", user_id=alice.id, unit_id=unit.id, status="active"),
            UnitMembership(id="membership-bob", user_id=bob.id, unit_id=unit.id, status="active"),
        ])
        session.commit()
    app.dependency_overrides[get_session] = lambda: factory()
    return TestClient(app), factory


def add_session(factory, *, session_id: str, token: str, user_id: str, last_seen_offset: int = 0):
    now = datetime.now(timezone.utc)
    with factory() as session:
        session.add(AuthSession(
            id=session_id,
            session_token_hash=_hash(token),
            user_id=user_id,
            unit_id="unit-1",
            current_project_id="project-1",
            auth_method="local",
            csrf_secret_encrypted={"ciphertext": f"csrf-{session_id}"},
            provider_tokens_encrypted=None,
            provider_sid=None,
            authorization_version=1,
            idle_expires_at=now + timedelta(minutes=30),
            absolute_expires_at=now + timedelta(hours=8),
            last_seen_at=now + timedelta(seconds=last_seen_offset),
        ))
        session.commit()


def csrf_token(client: TestClient) -> str:
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    return response.json()["csrf_token"]


def test_session_list_only_returns_current_users_active_sessions_and_never_token_hashes(monkeypatch):
    client, factory = build_client(monkeypatch)
    add_session(factory, session_id="alice-current-session", token="alice-current-token", user_id="alice")
    add_session(factory, session_id="alice-other-session", token="alice-other-token", user_id="alice", last_seen_offset=-60)
    add_session(factory, session_id="bob-session", token="bob-token", user_id="bob")
    client.cookies.set(SESSION_COOKIE, "alice-current-token")

    response = client.get("/api/auth/sessions")

    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert len(sessions) == 2
    assert {item["session_id"] for item in sessions} == {
        "alice-current-session",
        "alice-other-session",
    }
    assert next(item for item in sessions if item["is_current_session"])["session_id"] == "alice-current-session"
    assert all(item["current_project"] == {"id": "project-1", "name": "Project 1"} for item in sessions)
    payload_text = response.text
    assert "alice-current-token" not in payload_text
    assert _hash("alice-current-token") not in payload_text
    assert "bob-session" not in payload_text


def test_revoke_own_other_session_requires_csrf_and_records_security_audit(monkeypatch):
    client, factory = build_client(monkeypatch)
    add_session(factory, session_id="alice-current-session", token="alice-current-token", user_id="alice")
    add_session(factory, session_id="alice-other-session", token="alice-other-token", user_id="alice")
    client.cookies.set(SESSION_COOKIE, "alice-current-token")

    missing_csrf = client.post(
        "/api/auth/sessions/alice-other-session/revoke",
        headers={"Origin": "http://127.0.0.1"},
    )
    assert missing_csrf.status_code == 403

    response = client.post(
        "/api/auth/sessions/alice-other-session/revoke",
        headers={"Origin": "http://127.0.0.1", "X-CSRF-Token": csrf_token(client)},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "revoked": 1}
    with factory() as session:
        revoked = session.get(AuthSession, "alice-other-session")
        event = session.scalar(select(AuditEvent).where(
            AuditEvent.action == "auth.session.revoked",
            AuditEvent.resource_id == "alice-other-session",
        ))
        assert revoked.revoked_at is not None
        assert revoked.revoke_reason == "user_revoked"
        assert event is not None
        assert event.user_id == "alice"
        assert event.auth_method == "local"


def test_user_cannot_revoke_another_users_session(monkeypatch):
    client, factory = build_client(monkeypatch)
    add_session(factory, session_id="alice-current-session", token="alice-current-token", user_id="alice")
    add_session(factory, session_id="bob-session", token="bob-token", user_id="bob")
    client.cookies.set(SESSION_COOKIE, "alice-current-token")

    response = client.post(
        "/api/auth/sessions/bob-session/revoke",
        headers={"Origin": "http://127.0.0.1", "X-CSRF-Token": csrf_token(client)},
    )

    assert response.status_code == 404
    with factory() as session:
        assert session.get(AuthSession, "bob-session").revoked_at is None


def test_revoke_others_preserves_current_session_and_audits_every_revocation(monkeypatch):
    client, factory = build_client(monkeypatch)
    add_session(factory, session_id="alice-current-session", token="alice-current-token", user_id="alice")
    add_session(factory, session_id="alice-other-one", token="alice-other-token-1", user_id="alice")
    add_session(factory, session_id="alice-other-two", token="alice-other-token-2", user_id="alice")
    client.cookies.set(SESSION_COOKIE, "alice-current-token")

    response = client.post(
        "/api/auth/sessions/revoke-others",
        headers={"Origin": "http://127.0.0.1", "X-CSRF-Token": csrf_token(client)},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "revoked": 2}
    with factory() as session:
        assert session.get(AuthSession, "alice-current-session").revoked_at is None
        assert session.get(AuthSession, "alice-other-one").revoked_at is not None
        assert session.get(AuthSession, "alice-other-two").revoked_at is not None
        events = session.scalars(select(AuditEvent).where(
            AuditEvent.action == "auth.session.revoked",
            AuditEvent.user_id == "alice",
        )).all()
        assert {event.resource_id for event in events} == {"alice-other-one", "alice-other-two"}


def test_revoke_current_session_clears_browser_cookie(monkeypatch):
    client, factory = build_client(monkeypatch)
    add_session(factory, session_id="alice-current-session", token="alice-current-token", user_id="alice")
    client.cookies.set(SESSION_COOKIE, "alice-current-token")

    response = client.post(
        "/api/auth/sessions/alice-current-session/revoke",
        headers={"Origin": "http://127.0.0.1", "X-CSRF-Token": csrf_token(client)},
    )

    assert response.status_code == 200
    assert "iap_session=\"\"" in response.headers["set-cookie"]
    with factory() as session:
        assert session.get(AuthSession, "alice-current-session").revoked_at is not None
