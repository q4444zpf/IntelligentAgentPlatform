from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from authlib.jose import JsonWebKey, jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.identity.oidc import OidcProtocolError


@dataclass
class _AuthorizationCode:
    redirect_uri: str
    code_challenge: str
    nonce: str
    expires_at: float
    consumed: bool = False


class MockOidcProvider:
    """In-process OIDC provider used by protocol and integration tests only."""

    def __init__(self, *, issuer: str, client_id: str, code_ttl: int = 300) -> None:
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.code_ttl = code_ttl
        self._codes: dict[str, _AuthorizationCode] = {}
        self._kid = "mock-key-1"
        self._private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_bytes = self._private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        self._public_jwk = JsonWebKey.import_key(
            private_bytes,
            {"kty": "RSA", "kid": self._kid},
        ).as_dict(is_private=False)

    def metadata(self) -> dict[str, str]:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": f"{self.issuer}/authorize",
            "token_endpoint": f"{self.issuer}/token",
            "jwks_uri": f"{self.issuer}/jwks",
        }

    def jwks(self) -> dict[str, list[dict[str, str]]]:
        return {"keys": [self._public_jwk]}

    def authorize(self, params: dict[str, str]) -> str:
        if params.get("client_id") != self.client_id:
            raise OidcProtocolError("OIDC client is invalid")
        if not params.get("redirect_uri") or not urlsplit(params["redirect_uri"]).scheme:
            raise OidcProtocolError("OIDC redirect URI is invalid")
        if not params.get("code_challenge") or params.get("code_challenge_method") not in (None, "S256"):
            raise OidcProtocolError("OIDC PKCE challenge is invalid")
        if not params.get("nonce"):
            raise OidcProtocolError("OIDC nonce is required")
        code = secrets.token_urlsafe(24)
        self._codes[code] = _AuthorizationCode(
            redirect_uri=params["redirect_uri"],
            code_challenge=params["code_challenge"],
            nonce=params["nonce"],
            expires_at=time.time() + self.code_ttl,
        )
        return code

    def token(self, form: dict[str, str]) -> dict[str, str | int]:
        code = self._codes.get(form.get("code", ""))
        if code is None or code.consumed:
            raise OidcProtocolError("OIDC authorization code is invalid")
        if code.expires_at <= time.time():
            raise OidcProtocolError("OIDC authorization code is expired")
        if form.get("client_id") != self.client_id or form.get("redirect_uri") != code.redirect_uri:
            raise OidcProtocolError("OIDC authorization code request is invalid")
        verifier = form.get("code_verifier", "")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if not verifier or not secrets.compare_digest(challenge, code.code_challenge):
            raise OidcProtocolError("OIDC PKCE verifier is invalid")
        code.consumed = True
        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "sub": "mock-subject-1",
            "aud": self.client_id,
            "azp": self.client_id,
            "nonce": code.nonce,
            "iat": now,
            "exp": now + 300,
            "email": "mock.user@example.test",
            "name": "Mock User",
        }
        private_bytes = self._private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        id_token = jwt.encode({"alg": "RS256", "kid": self._kid}, claims, private_bytes).decode()
        return {"access_token": "mock-access-token", "token_type": "Bearer", "expires_in": 300, "id_token": id_token}
