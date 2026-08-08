import base64
import hashlib
import time
from dataclasses import replace
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from app.identity.oidc import OidcProtocolError
from tests.support.mock_oidc_provider import MockOidcProvider


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def test_mock_provider_issues_one_time_authorization_code_with_pkce():
    provider = MockOidcProvider(issuer="https://mock-oidc.example.test", client_id="iap-console")
    verifier = "verifier-123456789"
    code = provider.authorize(
        {
            "client_id": "iap-console",
            "redirect_uri": "http://127.0.0.1/auth/callback",
            "code_challenge": _challenge(verifier),
            "nonce": "nonce-1",
        }
    )

    payload = provider.token(
        {
            "code": code,
            "client_id": "iap-console",
            "redirect_uri": "http://127.0.0.1/auth/callback",
            "code_verifier": verifier,
        }
    )

    assert payload["token_type"] == "Bearer"
    assert isinstance(payload["id_token"], str)
    with pytest.raises(OidcProtocolError, match="authorization code"):
        provider.token(
            {
                "code": code,
                "client_id": "iap-console",
                "redirect_uri": "http://127.0.0.1/auth/callback",
                "code_verifier": verifier,
            }
        )


def test_mock_provider_rejects_wrong_pkce_verifier_and_expired_code():
    provider = MockOidcProvider(issuer="https://mock-oidc.example.test", client_id="iap-console", code_ttl=1)
    verifier = "verifier-123456789"
    code = provider.authorize(
        {
            "client_id": "iap-console",
            "redirect_uri": "http://127.0.0.1/auth/callback",
            "code_challenge": _challenge(verifier),
            "nonce": "nonce-1",
        }
    )
    with pytest.raises(OidcProtocolError, match="PKCE"):
        provider.token(
            {
                "code": code,
                "client_id": "iap-console",
                "redirect_uri": "http://127.0.0.1/auth/callback",
                "code_verifier": "wrong-verifier",
            }
        )

    expired = provider.authorize(
        {
            "client_id": "iap-console",
            "redirect_uri": "http://127.0.0.1/auth/callback",
            "code_challenge": _challenge(verifier),
            "nonce": "nonce-2",
        }
    )
    provider._codes[expired].expires_at = time.time() - 1
    with pytest.raises(OidcProtocolError, match="expired"):
        provider.token(
            {
                "code": expired,
                "client_id": "iap-console",
                "redirect_uri": "http://127.0.0.1/auth/callback",
                "code_verifier": verifier,
            }
        )


def test_mock_provider_completes_bff_login_callback_and_me(monkeypatch):
    from tests.identity.test_auth_api import build_client
    import app.identity.auth_router as auth_router
    from app.identity.models import ExternalIdentity

    provider = MockOidcProvider(issuer="https://mock-oidc.example.test", client_id="iap-console")
    client, factory = build_client()
    monkeypatch.setattr(
        auth_router,
        "settings",
        replace(
            auth_router.settings,
            oidc_issuer=provider.issuer,
            oidc_client_id=provider.client_id,
            oidc_redirect_uri="http://127.0.0.1/auth/callback",
            oidc_scope="openid profile email",
            session_cookie_secure=False,
        ),
    )
    with factory() as session:
        session.add(
            ExternalIdentity(
                id="external-mock-1",
                user_id="user-1",
                issuer=provider.issuer,
                subject="mock-subject-1",
                last_login_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

    async def metadata():
        return provider.metadata()

    monkeypatch.setattr(auth_router, "_oidc_metadata", metadata)

    class _Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url):
            assert url == provider.metadata()["jwks_uri"]
            return _Response(provider.jwks())

        async def post(self, url, data):
            assert url == provider.metadata()["token_endpoint"]
            return _Response(provider.token(data))

    monkeypatch.setattr(auth_router.httpx, "AsyncClient", lambda **_kwargs: _Client())

    login = client.get("/api/auth/login")
    assert login.status_code == 200
    params = parse_qs(urlsplit(login.json()["authorization_url"]).query)
    code = provider.authorize({key: values[0] for key, values in params.items()})

    callback = client.get(f"/api/auth/callback?code={code}&state={params['state'][0]}")
    assert callback.status_code == 200
    assert callback.json()["auth_method"] == "oidc"
    assert client.get("/api/auth/me").status_code == 200
