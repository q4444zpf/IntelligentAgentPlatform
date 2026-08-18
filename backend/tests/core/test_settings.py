from app.core import settings as settings_module
from app.core.settings import Settings, read_secret
import base64

import pytest


def _base64url_key(seed: int = 0) -> str:
    return base64.urlsafe_b64encode(bytes((value + seed) % 256 for value in range(32))).decode().rstrip("=")


def _configure_production(monkeypatch):
    monkeypatch.setenv("IAP_ENVIRONMENT", "production")
    monkeypatch.setenv("IAP_PUBLIC_BASE_URL", "https://console.example.test")
    monkeypatch.setenv("IAP_SESSION_COOKIE_SECURE", "true")
    monkeypatch.setenv("IAP_SESSION_HMAC_KEY", "hmac-key-that-is-at-least-thirty-two-bytes")
    monkeypatch.setenv("IAP_AUTH_ENCRYPTION_KEYS", f"k1:{_base64url_key()}")
    monkeypatch.setenv("OIDC_ISSUER", "https://identity.example.test")
    monkeypatch.setenv("OIDC_CLIENT_ID", "iap-console")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("OIDC_REDIRECT_URI", "https://console.example.test/auth/callback")


def _assert_exception_chain_excludes_secret(
    error: BaseException,
    secret: str | bytes,
) -> None:
    pending = [error]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        if isinstance(secret, str):
            assert secret not in str(current)
            assert secret not in repr(current)
        assert getattr(current, "object", None) != secret
        assert current.__cause__ is None
        assert current.__context__ is None


def test_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("IAP_ALLOW_DEV_IDENTITY", raising=False)
    settings = Settings.from_env()
    assert settings.database_url == "postgresql+psycopg://iap:iap@127.0.0.1:5432/iap"
    assert settings.allow_dev_identity is False


def test_reads_database_and_dev_identity_settings(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db/app")
    monkeypatch.setenv("IAP_ALLOW_DEV_IDENTITY", "true")
    monkeypatch.setenv(
        "IAP_DEV_IDENTITY_TRUSTED_CIDRS",
        "172.28.0.0/16, 10.20.0.0/24",
    )
    settings = Settings.from_env()
    assert settings.database_url.endswith("@db/app")
    assert settings.allow_dev_identity is True
    assert settings.dev_identity_trusted_cidrs == (
        "172.28.0.0/16",
        "10.20.0.0/24",
    )


def test_production_rejects_development_identity(monkeypatch):
    monkeypatch.setenv("IAP_ENVIRONMENT", "production")
    monkeypatch.setenv("IAP_ALLOW_DEV_IDENTITY", "true")
    with pytest.raises(ValueError, match="development identity"):
        Settings.from_env().validate_startup()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("IAP_PUBLIC_BASE_URL", "http://console.example.test", "public origin"),
        ("OIDC_ISSUER", "http://identity.example.test", "issuer"),
        ("IAP_SESSION_COOKIE_SECURE", "false", "secure Cookies"),
        ("OIDC_ISSUER", "https://mock.example.test", "Mock issuer"),
    ],
)
def test_production_rejects_insecure_identity_settings(monkeypatch, name, value, message):
    _configure_production(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=message):
        Settings.from_env().validate_startup()


@pytest.mark.parametrize(
    "missing_name",
    ["OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET", "OIDC_REDIRECT_URI"],
)
def test_production_rejects_missing_client_configuration(monkeypatch, missing_name):
    _configure_production(monkeypatch)
    monkeypatch.delenv(missing_name)
    with pytest.raises(ValueError, match="client configuration"):
        Settings.from_env().validate_startup()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("IAP_SESSION_HMAC_KEY", "too-short", "HMAC"),
        ("IAP_AUTH_ENCRYPTION_KEYS", "k1:YWJj", "encryption"),
    ],
)
def test_production_rejects_missing_or_short_auth_keys(monkeypatch, name, value, message):
    _configure_production(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=message):
        Settings.from_env().validate_startup()


def test_production_rejects_missing_auth_key(monkeypatch):
    _configure_production(monkeypatch)
    monkeypatch.delenv("IAP_AUTH_ENCRYPTION_KEYS")
    with pytest.raises(ValueError, match="encryption"):
        Settings.from_env().validate_startup()


def test_production_rejects_missing_hmac_key(monkeypatch):
    _configure_production(monkeypatch)
    monkeypatch.delenv("IAP_SESSION_HMAC_KEY")
    with pytest.raises(ValueError, match="HMAC"):
        Settings.from_env().validate_startup()


def test_production_rejects_redirect_from_another_origin(monkeypatch):
    _configure_production(monkeypatch)
    monkeypatch.setenv("OIDC_REDIRECT_URI", "https://another.example.test/auth/callback")
    with pytest.raises(ValueError, match="redirect origin"):
        Settings.from_env().validate_startup()


def test_rejects_unknown_environment(monkeypatch):
    monkeypatch.setenv("IAP_ENVIRONMENT", "staging")
    with pytest.raises(ValueError, match="environment"):
        Settings.from_env()


def test_rejects_duplicate_encryption_key_ids(monkeypatch):
    monkeypatch.setenv(
        "IAP_AUTH_ENCRYPTION_KEYS",
        f"k1:{_base64url_key()},k1:{_base64url_key(1)}",
    )
    with pytest.raises(ValueError, match="duplicate"):
        Settings.from_env()


def test_rejects_simultaneous_secret_and_secret_file(monkeypatch, tmp_path):
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("from-file", encoding="utf-8")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "from-environment")
    monkeypatch.setenv("OIDC_CLIENT_SECRET_FILE", str(secret_file))
    with pytest.raises(ValueError, match="both"):
        Settings.from_env()


def test_rejects_unreadable_secret_file(monkeypatch, tmp_path):
    monkeypatch.setenv("OIDC_CLIENT_SECRET_FILE", str(tmp_path / "missing.txt"))
    with pytest.raises(ValueError, match="secret file"):
        Settings.from_env()


def test_invalid_non_ascii_encryption_key_does_not_retain_secret_in_exception():
    secret = "not-a-base64-secret-with-a-non-ascii-character-密"
    with pytest.raises(ValueError, match="base64url") as error:
        settings_module._decode_base64url_key(secret)
    _assert_exception_chain_excludes_secret(error.value, secret)


def test_invalid_utf8_secret_file_does_not_retain_secret_in_exception(monkeypatch, tmp_path):
    secret_file = tmp_path / "secret.txt"
    secret = b"\xff\xfe\x00"
    secret_file.write_bytes(secret)
    monkeypatch.setenv("OIDC_CLIENT_SECRET_FILE", str(secret_file))
    with pytest.raises(ValueError, match="secret file") as error:
        read_secret("OIDC_CLIENT_SECRET")
    _assert_exception_chain_excludes_secret(error.value, secret)


def test_production_accepts_redirect_uri_with_default_https_port(monkeypatch):
    _configure_production(monkeypatch)
    monkeypatch.setenv(
        "OIDC_REDIRECT_URI",
        "https://console.example.test:443/auth/callback",
    )
    Settings.from_env().validate_startup()


def test_production_rejects_redirect_uri_with_explicit_zero_port(monkeypatch):
    _configure_production(monkeypatch)
    monkeypatch.setenv(
        "OIDC_REDIRECT_URI",
        "https://console.example.test:0/auth/callback",
    )
    with pytest.raises(ValueError, match="redirect origin"):
        Settings.from_env().validate_startup()


def test_settings_repr_redacts_secret_values(monkeypatch):
    _configure_production(monkeypatch)
    monkeypatch.setenv("IAP_SESSION_HMAC_KEY", "hmac-key-that-must-not-appear-in-repr")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "client-secret-that-must-not-appear-in-repr")
    settings = Settings.from_env()
    representation = repr(settings)
    assert "hmac-key-that-must-not-appear-in-repr" not in representation
    assert "client-secret-that-must-not-appear-in-repr" not in representation
