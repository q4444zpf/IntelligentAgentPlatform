from dataclasses import replace
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import get_session
from app.db.base import Base
from app.identity.auth_router import SESSION_COOKIE
from app.identity.catalogue import seed_builtin_catalogue
from app.identity.models import (
    AuthSession,
    ExternalIdentity,
    LocalCredential,
    Project,
    Role,
    Unit,
    UnitMembership,
    UnitMembershipRole,
    User,
)
from app.audit.models import AuditEvent
from app.identity.passwords import hash_password
from app.main import app


def build_client(monkeypatch):
    import app.identity.auth_router as auth_router

    configured = replace(auth_router.settings, session_cookie_secure=False)
    monkeypatch.setattr(auth_router, "settings", configured)
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
        admin = User(
            id="admin-1", display_name="Admin", email="admin@example.com",
            status="active", authorization_version=1,
        )
        local_user = User(
            id="local-1", display_name="Local User", email="local@example.com",
            status="active", authorization_version=1,
        )
        oidc_user = User(
            id="oidc-1", display_name="OIDC User", email="oidc@example.com",
            status="active", authorization_version=1,
        )
        session.add_all([unit, project, admin, local_user, oidc_user])
        session.add_all([
            UnitMembership(id="um-admin", user_id=admin.id, unit_id=unit.id, status="active"),
            UnitMembership(id="um-local", user_id=local_user.id, unit_id=unit.id, status="active"),
            UnitMembership(id="um-oidc", user_id=oidc_user.id, unit_id=unit.id, status="active"),
            LocalCredential(
                user_id=local_user.id,
                password_hash=hash_password("Current-password-1"),
                must_change_password=False,
                failed_attempts=0,
            ),
            LocalCredential(
                user_id=oidc_user.id,
                password_hash=hash_password("Current-password-1"),
                must_change_password=False,
                failed_attempts=0,
            ),
            ExternalIdentity(
                id="external-oidc-1", user_id=oidc_user.id,
                issuer="https://identity.example", subject="oidc-subject",
                claims={}, last_login_at=datetime.now(timezone.utc),
            ),
        ])
        seed_builtin_catalogue(session, unit.id)
        unit_admin = session.query(Role).filter_by(unit_id=unit.id, code="unit_admin").one()
        session.add(UnitMembershipRole(
            id="umr-admin", user_id=admin.id, unit_id=unit.id,
            role_id=unit_admin.id, scope_type="unit",
        ))
        session.commit()
    app.dependency_overrides[get_session] = lambda: factory()
    app.state.allow_dev_identity = True
    return TestClient(app), factory


def admin_headers():
    return {
        "X-User-ID": "admin-1",
        "X-Unit-ID": "unit-1",
        "X-Project-ID": "project-1",
        "X-User-Role": "unit_admin",
    }


def test_local_login_is_case_insensitive_for_email(monkeypatch):
    client, _factory = build_client(monkeypatch)

    response = client.post(
        "/api/auth/local/login",
        json={"email": "LOCAL@EXAMPLE.COM", "password": "Current-password-1"},
    )

    assert response.status_code == 200


def test_password_change_requires_csrf_token(monkeypatch):
    client, _factory = build_client(monkeypatch)
    login = client.post(
        "/api/auth/local/login",
        json={"email": "local@example.com", "password": "Current-password-1"},
    )
    assert login.status_code == 200

    response = client.post(
        "/api/auth/password/change",
        headers={"Origin": "http://127.0.0.1"},
        json={"current_password": "Current-password-1", "new_password": "Changed-password-2"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF token is required"


def test_password_hash_rejects_unbounded_pbkdf2_parameters():
    from app.identity.passwords import verify_password

    assert verify_password("password", "pbkdf2-sha256$999999999$YWJj$YWJj") is False


def test_must_change_password_blocks_business_api_until_password_is_changed(monkeypatch,):
    client, factory = build_client(monkeypatch)
    with factory() as session:
        session.get(LocalCredential, "local-1").must_change_password = True
        session.commit()
    login = client.post(
        "/api/auth/local/login",
        json={"email": "local@example.com", "password": "Current-password-1"},
    )
    assert login.status_code == 200

    response = client.get("/api/agents")

    assert response.status_code == 403
    assert response.json()["detail"] == "PASSWORD_CHANGE_REQUIRED"


def test_local_login_creates_cookie_session_without_storing_plaintext_password(monkeypatch):
    client, factory = build_client(monkeypatch)

    response = client.post(
        "/api/auth/local/login",
        json={"email": "local@example.com", "password": "Current-password-1"},
    )

    assert response.status_code == 200
    assert response.json()["auth_method"] == "local"
    assert response.cookies.get(SESSION_COOKIE)
    with factory() as session:
        credential = session.get(LocalCredential, "local-1")
        auth = session.query(AuthSession).filter_by(user_id="local-1").one()
        assert credential.password_hash != "Current-password-1"
        assert credential.failed_attempts == 0
        assert auth.auth_method == "local"


def test_local_login_rejects_oidc_bound_user_and_does_not_modify_credential(monkeypatch):
    client, factory = build_client(monkeypatch)

    response = client.post(
        "/api/auth/local/login",
        json={"email": "oidc@example.com", "password": "Current-password-1"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "OIDC users must use unified login"
    with factory() as session:
        assert session.get(LocalCredential, "oidc-1").failed_attempts == 0


def test_failed_local_login_increments_failure_count(monkeypatch):
    client, factory = build_client(monkeypatch)

    response = client.post(
        "/api/auth/local/login",
        json={"email": "local@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"
    with factory() as session:
        assert session.get(LocalCredential, "local-1").failed_attempts == 1


def test_password_change_rotates_credential_and_revokes_existing_sessions(monkeypatch):
    client, factory = build_client(monkeypatch)
    login = client.post(
        "/api/auth/local/login",
        json={"email": "local@example.com", "password": "Current-password-1"},
    )
    assert login.status_code == 200
    token = login.cookies.get(SESSION_COOKIE)
    client.cookies.set(SESSION_COOKIE, token)
    csrf = client.get("/api/auth/me").json()["csrf_token"]

    response = client.post(
        "/api/auth/password/change",
        headers={"Origin": "http://127.0.0.1", "X-CSRF-Token": csrf},
        json={"current_password": "Current-password-1", "new_password": "Changed-password-2"},
    )

    assert response.status_code == 200
    with factory() as session:
        user = session.get(User, "local-1")
        credential = session.get(LocalCredential, "local-1")
        sessions = session.query(AuthSession).filter_by(user_id="local-1").all()
        assert user.authorization_version == 2
        assert credential.must_change_password is False
        assert credential.password_hash != "Changed-password-2"
        assert all(item.revoked_at is not None for item in sessions)
    assert client.post(
        "/api/auth/local/login",
        json={"email": "local@example.com", "password": "Current-password-1"},
    ).status_code == 401
    assert client.post(
        "/api/auth/local/login",
        json={"email": "local@example.com", "password": "Changed-password-2"},
    ).status_code == 200


def test_admin_password_reset_marks_first_change_and_revokes_target_sessions(monkeypatch):
    client, factory = build_client(monkeypatch)
    now = datetime.now(timezone.utc)
    with factory() as session:
        session.add(AuthSession(
            id="old-local-session", session_token_hash="hash", user_id="local-1",
            unit_id="unit-1", current_project_id="project-1", auth_method="local",
            csrf_secret_encrypted={"ciphertext": "csrf"}, provider_tokens_encrypted=None,
            provider_sid=None, authorization_version=1,
            idle_expires_at=now + timedelta(minutes=30),
            absolute_expires_at=now + timedelta(hours=8), last_seen_at=now,
        ))
        session.commit()

    response = client.post(
        "/api/identity/users/local-1/password-reset",
        headers=admin_headers(),
        json={"new_password": "Reset-password-3"},
    )

    assert response.status_code == 200
    assert response.json() == {"user_id": "local-1", "must_change_password": True}
    with factory() as session:
        user = session.get(User, "local-1")
        credential = session.get(LocalCredential, "local-1")
        auth = session.get(AuthSession, "old-local-session")
        assert user.authorization_version == 2
        assert credential.must_change_password is True
        assert credential.password_hash != "Reset-password-3"
        assert auth.revoked_at is not None


def test_admin_password_reset_records_security_audit_event(monkeypatch):
    client, factory = build_client(monkeypatch)

    response = client.post(
        "/api/identity/users/local-1/password-reset",
        headers=admin_headers(),
        json={"new_password": "Reset-password-3"},
    )

    assert response.status_code == 200
    with factory() as session:
        event = session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "auth.password.reset",
                AuditEvent.resource_id == "local-1",
            )
        )
        assert event is not None
        assert event.category == "security"
        assert event.source == "auth"
        assert event.status == "succeeded"
        assert event.user_id == "admin-1"
        assert event.unit_id == "unit-1"
        assert event.metadata_json.get("target_user_id") == "local-1"


def test_admin_cannot_reset_password_for_oidc_bound_user(monkeypatch):
    client, _factory = build_client(monkeypatch)

    response = client.post(
        "/api/identity/users/oidc-1/password-reset",
        headers=admin_headers(),
        json={"new_password": "Reset-password-3"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "OIDC users do not have local passwords"
