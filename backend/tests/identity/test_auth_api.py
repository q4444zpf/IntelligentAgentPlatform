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
    ExternalIdentity,
    OidcLoginTransaction,
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


def test_auth_responses_disable_caching(monkeypatch):
    from dataclasses import replace
    import app.identity.auth_router as auth_router
    monkeypatch.setattr(auth_router, "settings", replace(auth_router.settings, allow_dev_identity=True, environment="development"))
    client, _factory = build_client()
    response = client.post(
        "/api/auth/dev/login",
        headers={"X-User-ID": "user-1", "X-Unit-ID": "unit-1", "X-Project-ID": "project-1"},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def test_logout_rejects_cross_origin_cookie_request(monkeypatch):
    from dataclasses import replace
    import app.identity.auth_router as auth_router

    client, factory = build_client()
    monkeypatch.setattr(auth_router, "settings", replace(auth_router.settings, allow_dev_identity=True, environment="development", public_base_url="http://127.0.0.1"))
    login = client.post("/api/auth/dev/login", headers={"X-User-ID": "user-1", "X-Unit-ID": "unit-1", "X-Project-ID": "project-1"})
    assert login.status_code == 200

    response = client.post("/api/auth/logout", headers={"Origin": "https://evil.example"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Origin is not allowed"


def test_cookie_backed_identity_admin_request_rejects_cross_origin(monkeypatch):
    from dataclasses import replace
    import app.identity.auth_router as auth_router
    import app.main as main_module

    client, _factory = build_client()
    configured = replace(auth_router.settings, allow_dev_identity=True, environment="development", public_base_url="http://127.0.0.1", session_cookie_secure=False)
    monkeypatch.setattr(auth_router, "settings", configured)
    monkeypatch.setattr(main_module, "settings", configured)
    login = client.post("/api/auth/dev/login", headers={"X-User-ID": "user-1", "X-Unit-ID": "unit-1", "X-Project-ID": "project-1"})
    assert login.status_code == 200
    client.cookies.set(SESSION_COOKIE, "session-for-origin-test")

    response = client.post("/api/identity/users", headers={"Origin": "https://evil.example", "X-User-ID": "user-1", "X-Unit-ID": "unit-1", "X-Project-ID": "project-1", "X-User-Role": "unit_admin"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Origin is not allowed"


def test_oidc_nonce_must_match_transaction_hash():
    from app.identity.auth_router import _validate_nonce

    _validate_nonce({"nonce": "expected-nonce"}, _hash("expected-nonce"))

    try:
        _validate_nonce({"nonce": "wrong-nonce"}, _hash("expected-nonce"))
    except ValueError as error:
        assert str(error) == "OIDC nonce mismatch"
    else:
        raise AssertionError("nonce mismatch should be rejected")


def test_oidc_client_claims_require_matching_audience_and_azp():
    from app.identity.auth_router import _validate_client_claims

    _validate_client_claims({"aud": ["iap-console"], "azp": "iap-console"}, "iap-console")

    for claims in ({"aud": ["other-client"], "azp": "iap-console"}, {"aud": ["iap-console"], "azp": "other-client"}):
        try:
            _validate_client_claims(claims, "iap-console")
        except ValueError as error:
            assert str(error) == "OIDC client claims are invalid"
        else:
            raise AssertionError("invalid client claims should be rejected")


def test_oidc_algorithm_and_time_claims_are_strictly_validated():
    from app.identity.auth_router import _validate_algorithm, _validate_time_claims

    _validate_algorithm({"alg": "RS256"})
    try:
        _validate_algorithm({"alg": "none"})
    except ValueError as error:
        assert str(error) == "OIDC signing algorithm is invalid"
    else:
        raise AssertionError("unsafe signing algorithm should be rejected")

    now = datetime.now(timezone.utc).timestamp()
    _validate_time_claims({"iat": now - 10, "exp": now + 300}, now=now, clock_skew=60)
    for claims in ({"iat": now + 120, "exp": now + 300}, {"iat": now - 10, "exp": now - 120}, {"iat": now - 10, "exp": now + 300, "nbf": now + 120}):
        try:
            _validate_time_claims(claims, now=now, clock_skew=60)
        except ValueError as error:
            assert str(error) == "OIDC time claims are invalid"
        else:
            raise AssertionError("invalid time claims should be rejected")


def test_oidc_browser_correlation_requires_matching_transaction_cookie():
    from app.identity.auth_router import _validate_browser_correlation

    _validate_browser_correlation("browser-token", _hash("browser-token"))

    try:
        _validate_browser_correlation("wrong-token", _hash("browser-token"))
    except ValueError as error:
        assert str(error) == "OIDC browser correlation mismatch"
    else:
        raise AssertionError("browser correlation mismatch should be rejected")


def test_oidc_login_sets_browser_correlation_cookie_and_pkce(monkeypatch):
    from dataclasses import replace
    from unittest.mock import AsyncMock
    import app.identity.auth_router as auth_router

    client, factory = build_client()
    monkeypatch.setattr(auth_router, "settings", replace(auth_router.settings, oidc_issuer="https://mock-oidc.example.test", oidc_client_id="iap-console", oidc_redirect_uri="http://127.0.0.1/auth/callback", oidc_scope="openid profile"))
    monkeypatch.setattr(auth_router, "_oidc_metadata", AsyncMock(return_value={"authorization_endpoint": "https://mock-oidc.example.test/authorize"}))

    response = client.get("/api/auth/login")

    assert response.status_code == 200
    payload = response.json()
    assert payload["code_challenge"]
    assert "state=" in payload["authorization_url"]
    assert "code_challenge_method=S256" in payload["authorization_url"]
    assert "iap_oidc_browser=" in response.headers["set-cookie"]
    with factory() as session:
        transaction = session.query(auth_router.OidcLoginTransaction).one()
        assert transaction.browser_correlation_hash


def test_oidc_callback_rejects_missing_browser_cookie_before_token_exchange(monkeypatch):
    import app.identity.auth_router as auth_router
    from app.identity.models import OidcLoginTransaction

    client, factory = build_client()
    now = datetime.now(timezone.utc)
    with factory() as session:
        session.add(OidcLoginTransaction(
            id="oidc-tx-1", state_hash=_hash("state-1"), nonce_hash=_hash("nonce-1"),
            browser_correlation_hash=_hash("browser-1"), pkce_verifier_encrypted={"value": "verifier"},
            issuer="https://mock-oidc.example.test", client_id="iap-console",
            redirect_uri="http://127.0.0.1/auth/callback", return_to="/dashboard",
            expires_at=now + timedelta(minutes=5),
        ))
        session.commit()

    metadata = lambda: (_ for _ in ()).throw(AssertionError("token exchange must not start"))
    monkeypatch.setattr(auth_router, "_oidc_metadata", metadata)

    response = client.get("/api/auth/callback?code=code-1&state=state-1")

    assert response.status_code == 400
    assert response.json()["detail"] == "OIDC browser correlation mismatch"


def test_oidc_callback_creates_platform_session_for_bound_identity(monkeypatch):
    import app.identity.auth_router as auth_router
    from unittest.mock import AsyncMock
    from dataclasses import replace

    client, factory = build_client()
    monkeypatch.setattr(auth_router, "settings", replace(auth_router.settings, session_cookie_secure=False))
    now = datetime.now(timezone.utc)
    with factory() as session:
        session.add_all([
            ExternalIdentity(id="external-1", user_id="user-1", issuer="https://mock-oidc.example.test", subject="subject-1", last_login_at=now),
            OidcLoginTransaction(id="oidc-tx-2", state_hash=_hash("state-2"), nonce_hash=_hash("nonce-2"), browser_correlation_hash=_hash("browser-2"), pkce_verifier_encrypted={"value": "verifier"}, issuer="https://mock-oidc.example.test", client_id="iap-console", redirect_uri="http://127.0.0.1/auth/callback", return_to="/dashboard", expires_at=now + timedelta(minutes=5)),
        ])
        session.commit()

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id_token": "mock-id-token"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(auth_router, "_oidc_metadata", AsyncMock(return_value={"token_endpoint": "https://mock-oidc.example.test/token"}))
    monkeypatch.setattr(auth_router, "_validate_id_token", AsyncMock(return_value={"iss": "https://mock-oidc.example.test", "sub": "subject-1", "nonce": "nonce-2"}))
    monkeypatch.setattr(auth_router.httpx, "AsyncClient", lambda **_kwargs: FakeClient())
    client.cookies.set(auth_router.OIDC_BROWSER_COOKIE, "browser-2")

    response = client.get("/api/auth/callback?code=code-2&state=state-2")

    assert response.status_code == 200
    assert response.json()["auth_method"] == "oidc"
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["id"] == "user-1"
