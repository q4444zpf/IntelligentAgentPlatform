from datetime import datetime, timezone

import httpx
import pytest

from app.identity.oidc import (
    OidcProtocolError,
    build_authorization_url,
    validate_discovery_metadata,
    validate_id_token_claims,
    exchange_authorization_code,
)


def test_build_authorization_url_contains_pkce_and_correlation_parameters():
    url = build_authorization_url(
        authorization_endpoint="https://issuer.example/authorize",
        client_id="iap-console",
        redirect_uri="https://console.example/auth/callback",
        scope="openid profile email",
        state="state-1",
        nonce="nonce-1",
        code_challenge="challenge-1",
    )

    assert "response_type=code" in url
    assert "client_id=iap-console" in url
    assert "state=state-1" in url
    assert "nonce=nonce-1" in url
    assert "code_challenge=challenge-1" in url
    assert "code_challenge_method=S256" in url


def test_discovery_requires_exact_configured_issuer():
    metadata = {
        "issuer": "https://issuer.example",
        "authorization_endpoint": "https://issuer.example/authorize",
        "token_endpoint": "https://issuer.example/token",
        "jwks_uri": "https://issuer.example/jwks",
    }

    assert validate_discovery_metadata(metadata, "https://issuer.example")["issuer"] == metadata["issuer"]

    with pytest.raises(OidcProtocolError, match="issuer mismatch"):
        validate_discovery_metadata(metadata, "https://other.example")


def test_id_token_claims_require_issuer_audience_azp_nonce_and_valid_time():
    now = datetime.now(timezone.utc).timestamp()
    claims = {
        "iss": "https://issuer.example",
        "sub": "subject-1",
        "aud": ["iap-console"],
        "azp": "iap-console",
        "nonce": "nonce-1",
        "iat": now - 10,
        "exp": now + 300,
    }

    validated = validate_id_token_claims(
        claims,
        issuer="https://issuer.example",
        client_id="iap-console",
        expected_nonce="nonce-1",
        now=now,
        clock_skew=60,
    )
    assert validated["sub"] == "subject-1"

    for key, value in (("iss", "https://other.example"), ("nonce", "wrong"), ("azp", "other")):
        invalid = {**claims, key: value}
        with pytest.raises(OidcProtocolError):
            validate_id_token_claims(
                invalid,
                issuer="https://issuer.example",
                client_id="iap-console",
                expected_nonce="nonce-1",
                now=now,
                clock_skew=60,
            )


def test_id_token_claims_reject_expired_token():
    now = datetime.now(timezone.utc).timestamp()
    claims = {
        "iss": "https://issuer.example",
        "sub": "subject-1",
        "aud": "iap-console",
        "azp": "iap-console",
        "nonce": "nonce-1",
        "iat": now - 500,
        "exp": now - 100,
    }

    with pytest.raises(OidcProtocolError, match="time claims"):
        validate_id_token_claims(
            claims,
            issuer="https://issuer.example",
            client_id="iap-console",
            expected_nonce="nonce-1",
            now=now,
            clock_skew=60,
        )


@pytest.mark.anyio
async def test_exchange_authorization_code_rejects_provider_error_and_missing_id_token():
    async def provider(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/error"):
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(200, json={"access_token": "opaque"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    with pytest.raises(OidcProtocolError, match="token exchange failed"):
        await exchange_authorization_code(client, "https://issuer.example/error", {"code": "bad"})
    with pytest.raises(OidcProtocolError, match="ID token"):
        await exchange_authorization_code(client, "https://issuer.example/token", {"code": "ok"})
    await client.aclose()


def test_id_token_claims_reject_non_rsa_algorithm():
    now = datetime.now(timezone.utc).timestamp()
    claims = {
        "iss": "https://issuer.example",
        "sub": "subject-1",
        "aud": "iap-console",
        "azp": "iap-console",
        "nonce": "nonce-1",
        "iat": now - 10,
        "exp": now + 300,
    }

    with pytest.raises(OidcProtocolError, match="signing algorithm"):
        validate_id_token_claims(
            claims,
            header={"alg": "none"},
            issuer="https://issuer.example",
            client_id="iap-console",
            expected_nonce="nonce-1",
            now=now,
        )
