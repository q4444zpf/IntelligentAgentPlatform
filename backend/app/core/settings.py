import base64
from dataclasses import dataclass, field
import os
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlsplit


VALID_ENVIRONMENTS = frozenset({"development", "production", "test"})


def read_secret(name: str) -> str | None:
    """Read a secret from its environment variable or its optional file."""
    value = os.getenv(name)
    file_name = f"{name}_FILE"
    file_path = os.getenv(file_name)
    if value is not None and file_path is not None:
        raise ValueError(f"{name} and {file_name} cannot both be set")
    if file_path is None:
        return value
    try:
        return Path(file_path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        pass
    raise ValueError(f"unable to read secret file for {name}")


def _read_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes"}


def _read_positive_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a positive number") from error
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive number")
    return parsed


def _read_nonnegative_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a non-negative integer") from error
    if parsed < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return parsed


def _decode_base64url_key(value: str) -> bytes:
    try:
        padded = value + "=" * (-len(value) % 4)
        return base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError):
        pass
    raise ValueError("encryption key must be base64url encoded")


def _read_encryption_keys(value: str | None) -> dict[str, bytes]:
    if not value:
        return {}
    keys: dict[str, bytes] = {}
    for item in value.split(","):
        key_id, separator, encoded_key = item.strip().partition(":")
        if not separator or not key_id or not encoded_key:
            raise ValueError("encryption keys must use kid:base64url-key format")
        if key_id in keys:
            raise ValueError("duplicate encryption key ID")
        key = _decode_base64url_key(encoded_key)
        if len(key) != 32:
            raise ValueError("encryption keys must be 32 bytes")
        keys[key_id] = key
    return keys


def _is_https_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlsplit(value)
    return parsed.scheme == "https" and bool(parsed.hostname)


def _origin(value: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    default_port = 443 if scheme == "https" else 80 if scheme == "http" else None
    port = parsed.port
    return scheme, (parsed.hostname or "").lower(), default_port if port is None else port


@dataclass(frozen=True)
class Settings:
    database_url: str
    allow_dev_identity: bool
    environment: str
    public_base_url: str | None
    session_cookie_secure: bool
    session_hmac_key: str | None = field(repr=False)
    auth_encryption_keys: dict[str, bytes] = field(repr=False)
    oidc_issuer: str | None
    oidc_client_id: str | None
    oidc_client_secret: str | None = field(repr=False)
    oidc_redirect_uri: str | None
    oidc_scope: str
    oidc_connect_timeout_seconds: float
    oidc_read_timeout_seconds: float
    oidc_clock_skew_seconds: int
    trusted_proxy_cidrs: tuple[str, ...]
    dev_identity_trusted_cidrs: tuple[str, ...]

    @property
    def current_encryption_key_id(self) -> str | None:
        return next(iter(self.auth_encryption_keys), None)

    @property
    def previous_encryption_keys(self) -> dict[str, bytes]:
        current_key_id = self.current_encryption_key_id
        return {
            key_id: key
            for key_id, key in self.auth_encryption_keys.items()
            if key_id != current_key_id
        }

    @classmethod
    def from_env(cls) -> "Settings":
        environment = os.getenv("IAP_ENVIRONMENT", "development").lower()
        if environment not in VALID_ENVIRONMENTS:
            raise ValueError("unknown environment")
        encryption_keys = _read_encryption_keys(read_secret("IAP_AUTH_ENCRYPTION_KEYS"))
        trusted_proxy_cidrs = tuple(
            cidr.strip()
            for cidr in os.getenv("TRUSTED_PROXY_CIDRS", "").split(",")
            if cidr.strip()
        )
        dev_identity_trusted_cidrs = tuple(
            cidr.strip()
            for cidr in os.getenv("IAP_DEV_IDENTITY_TRUSTED_CIDRS", "").split(",")
            if cidr.strip()
        )
        return cls(
            database_url=os.getenv(
                "DATABASE_URL",
                "postgresql+psycopg://iap:iap@127.0.0.1:5432/iap",
            ),
            allow_dev_identity=_read_bool("IAP_ALLOW_DEV_IDENTITY", False),
            environment=environment,
            public_base_url=os.getenv("IAP_PUBLIC_BASE_URL"),
            session_cookie_secure=_read_bool("IAP_SESSION_COOKIE_SECURE", True),
            session_hmac_key=read_secret("IAP_SESSION_HMAC_KEY"),
            auth_encryption_keys=MappingProxyType(encryption_keys),
            oidc_issuer=os.getenv("OIDC_ISSUER"),
            oidc_client_id=os.getenv("OIDC_CLIENT_ID"),
            oidc_client_secret=read_secret("OIDC_CLIENT_SECRET"),
            oidc_redirect_uri=os.getenv("OIDC_REDIRECT_URI"),
            oidc_scope=os.getenv("OIDC_SCOPE", "openid profile email"),
            oidc_connect_timeout_seconds=_read_positive_float(
                "OIDC_CONNECT_TIMEOUT_SECONDS", 5.0
            ),
            oidc_read_timeout_seconds=_read_positive_float("OIDC_READ_TIMEOUT_SECONDS", 10.0),
            oidc_clock_skew_seconds=_read_nonnegative_int("OIDC_CLOCK_SKEW_SECONDS", 60),
            trusted_proxy_cidrs=trusted_proxy_cidrs,
            dev_identity_trusted_cidrs=dev_identity_trusted_cidrs,
        )

    def validate_startup(self) -> None:
        if self.environment != "production":
            return
        if self.allow_dev_identity:
            raise ValueError("production startup does not permit development identity")
        if not _is_https_url(self.public_base_url):
            raise ValueError("production requires an HTTPS public origin")
        if not _is_https_url(self.oidc_issuer):
            raise ValueError("production requires an HTTPS issuer")
        issuer_host = urlsplit(self.oidc_issuer).hostname or ""
        if issuer_host.lower().startswith("mock"):
            raise ValueError("production does not permit a Mock issuer")
        if not self.session_cookie_secure:
            raise ValueError("production requires secure Cookies")
        if not self.session_hmac_key or len(self.session_hmac_key.encode("utf-8")) < 32:
            raise ValueError("production requires an HMAC key of at least 32 bytes")
        if not self.auth_encryption_keys:
            raise ValueError("production requires an encryption key")
        if not all(
            (self.oidc_client_id, self.oidc_client_secret, self.oidc_redirect_uri)
        ):
            raise ValueError("production requires complete OIDC client configuration")
        if not _is_https_url(self.oidc_redirect_uri):
            raise ValueError("production requires an HTTPS redirect URI")
        if _origin(self.public_base_url) != _origin(self.oidc_redirect_uri):
            raise ValueError("OIDC redirect origin must match the public origin")


settings = Settings.from_env()
