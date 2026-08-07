from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.core.database import get_session
from app.db.base import Base
from app.identity.auth_router import SESSION_COOKIE, _hash
from app.identity.catalogue import seed_builtin_catalogue
from app.identity.models import (
    AuthSession,
    Project,
    ProjectMembership,
    Unit,
    UnitMembership,
    UnitMembershipRole,
    User,
    Role,
)
from app.main import app


def aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def build_client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        unit = Unit(id="unit-1", code="u1", name="Unit 1", status="active")
        project = Project(id="project-1", unit_id=unit.id, code="p1", name="Project 1", status="active")
        other_project = Project(id="project-2", unit_id=unit.id, code="p2", name="Project 2", status="active")
        user = User(id="user-1", display_name="Alice", email=None, status="active", authorization_version=1)
        session.add_all([unit, project, other_project, user, UnitMembership(id="um-1", user_id=user.id, unit_id=unit.id, status="active")])
        session.commit()
    app.dependency_overrides[get_session] = lambda: factory()
    return TestClient(app), factory


def test_auth_me_rejects_idle_expired_session():
    client, factory = build_client()
    token = "expired-token"
    now = datetime.now(timezone.utc)
    with factory() as session:
        session.add(AuthSession(
            id="session-1",
            session_token_hash=_hash(token),
            user_id="user-1",
            unit_id="unit-1",
            current_project_id="project-1",
            auth_method="oidc",
            csrf_secret_encrypted={"ciphertext": "csrf"},
            provider_tokens_encrypted=None,
            provider_sid=None,
            authorization_version=1,
            idle_expires_at=now - timedelta(seconds=1),
            absolute_expires_at=now + timedelta(hours=1),
            last_seen_at=now - timedelta(minutes=31),
        ))
        session.commit()

    client.cookies.set(SESSION_COOKIE, token)

    response = client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.json()["detail"] == "Session has expired"


def test_auth_me_renews_idle_expiry_for_valid_session():
    client, factory = build_client()
    token = "valid-token"
    now = datetime.now(timezone.utc)
    with factory() as session:
        session.add(AuthSession(
            id="session-1",
            session_token_hash=_hash(token),
            user_id="user-1",
            unit_id="unit-1",
            current_project_id="project-1",
            auth_method="oidc",
            csrf_secret_encrypted={"ciphertext": "csrf"},
            provider_tokens_encrypted=None,
            provider_sid=None,
            authorization_version=1,
            idle_expires_at=now + timedelta(minutes=1),
            absolute_expires_at=now + timedelta(hours=1),
            last_seen_at=now - timedelta(minutes=10),
        ))
        session.commit()

    client.cookies.set(SESSION_COOKIE, token)

    response = client.get("/api/auth/me")

    assert response.status_code == 200
    with factory() as session:
        auth = session.get(AuthSession, "session-1")
        assert aware(auth.idle_expires_at) > now + timedelta(minutes=20)


def test_auth_me_returns_current_project_and_project_list():
    client, factory = build_client()
    token = "context-token"
    now = datetime.now(timezone.utc)
    with factory() as session:
        session.add(AuthSession(
            id="session-1",
            session_token_hash=_hash(token),
            user_id="user-1",
            unit_id="unit-1",
            current_project_id="project-1",
            auth_method="oidc",
            csrf_secret_encrypted={"ciphertext": "csrf"},
            provider_tokens_encrypted=None,
            provider_sid=None,
            authorization_version=1,
            idle_expires_at=now + timedelta(minutes=30),
            absolute_expires_at=now + timedelta(hours=1),
            last_seen_at=now,
        ))
        session.commit()
    client.cookies.set(SESSION_COOKIE, token)

    response = client.get("/api/auth/me")

    assert response.status_code == 200
    payload = response.json()
    assert payload["current_project"] == {"id": "project-1", "name": "Project 1"}
    assert payload["projects"] == [
        {"id": "project-1", "name": "Project 1"},
        {"id": "project-2", "name": "Project 2"},
    ]


def test_auth_me_returns_authorization_menu_and_session_context():
    client, factory = build_client()
    token = "authorization-context-token"
    now = datetime.now(timezone.utc)
    with factory() as session:
        seed_builtin_catalogue(session, "unit-1")
        unit_admin = session.scalar(
            session.query(Role).where(Role.unit_id == "unit-1", Role.code == "unit_admin").statement
        )
        session.add_all([
            ProjectMembership(
                id="pm-1",
                user_id="user-1",
                unit_id="unit-1",
                project_id="project-1",
                status="active",
            ),
            UnitMembershipRole(
                id="umr-1",
                user_id="user-1",
                unit_id="unit-1",
                role_id=unit_admin.id,
                scope_type="unit",
            ),
            AuthSession(
                id="session-1",
                session_token_hash=_hash(token),
                user_id="user-1",
                unit_id="unit-1",
                current_project_id="project-1",
                auth_method="oidc",
                csrf_secret_encrypted={"ciphertext": "csrf-secret"},
                provider_tokens_encrypted=None,
                provider_sid=None,
                authorization_version=1,
                idle_expires_at=now + timedelta(minutes=30),
                absolute_expires_at=now + timedelta(hours=1),
                last_seen_at=now,
            ),
        ])
        session.commit()
    client.cookies.set(SESSION_COOKIE, token)

    response = client.get("/api/auth/me")

    assert response.status_code == 200
    payload = response.json()
    assert payload["roles"] == ["unit_admin"]
    assert {"code": "identity.read", "target": "unit"} in payload["permissions"]
    assert {"code": "agent.run", "target": "current_project"} in payload["permissions"]
    assert any(menu["route_key"] == "users" for menu in payload["menus"])
    assert any(menu["route_key"] == "chat" for menu in payload["menus"])
    assert payload["csrf_token"]
    assert payload["session"]["idle_expires_at"]
    assert payload["session"]["absolute_expires_at"]


def test_oidc_nonce_must_match_transaction_hash():
    from app.identity.auth_router import _validate_nonce

    _validate_nonce({"nonce": "expected-nonce"}, _hash("expected-nonce"))

    try:
        _validate_nonce({"nonce": "wrong-nonce"}, _hash("expected-nonce"))
    except ValueError as error:
        assert str(error) == "OIDC nonce mismatch"
    else:
        raise AssertionError("nonce mismatch should be rejected")
