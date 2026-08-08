from __future__ import annotations

import hmac
from collections.abc import Mapping
from time import time
from urllib.parse import urlencode

import httpx


class OidcProtocolError(ValueError):
    """Raised when an OIDC protocol response violates the configured contract."""


def build_authorization_url(
    *,
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    scope: str,
    state: str,
    nonce: str,
    code_challenge: str,
) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{authorization_endpoint}?{query}"


def validate_discovery_metadata(
    metadata: Mapping[str, object], configured_issuer: str
) -> dict[str, object]:
    if metadata.get("issuer") != configured_issuer:
        raise OidcProtocolError("OIDC issuer mismatch")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not isinstance(metadata.get(key), str) or not metadata[key]:
            raise OidcProtocolError(f"OIDC discovery missing {key}")
    return dict(metadata)


def validate_id_token_claims(
    claims: Mapping[str, object],
    *,
    header: Mapping[str, object] | None = None,
    issuer: str,
    client_id: str,
    expected_nonce: str,
    now: float | None = None,
    clock_skew: int = 60,
) -> dict[str, object]:
    if header is not None and header.get("alg") not in {"RS256", "RS384", "RS512"}:
        raise OidcProtocolError("OIDC signing algorithm is invalid")
    if claims.get("iss") != issuer:
        raise OidcProtocolError("OIDC issuer claim mismatch")
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if client_id not in audiences or claims.get("azp") != client_id:
        raise OidcProtocolError("OIDC client claims are invalid")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise OidcProtocolError("OIDC subject is missing")
    if not hmac.compare_digest(str(claims.get("nonce", "")), expected_nonce):
        raise OidcProtocolError("OIDC nonce mismatch")
    current = time() if now is None else now
    try:
        issued_at = float(claims["iat"])
        expires_at = float(claims["exp"])
        not_before = float(claims.get("nbf", issued_at))
    except (KeyError, TypeError, ValueError):
        raise OidcProtocolError("OIDC time claims are invalid") from None
    if (
        issued_at > current + clock_skew
        or expires_at < current - clock_skew
        or not_before > current + clock_skew
        or expires_at <= issued_at
    ):
        raise OidcProtocolError("OIDC time claims are invalid")
    return dict(claims)


async def exchange_authorization_code(
    client: httpx.AsyncClient,
    token_endpoint: str,
    form: Mapping[str, str],
) -> dict[str, object]:
    try:
        response = await client.post(token_endpoint, data=dict(form))
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise OidcProtocolError("OIDC token exchange failed") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("id_token"), str):
        raise OidcProtocolError("OIDC response did not contain an ID token")
    return payload
