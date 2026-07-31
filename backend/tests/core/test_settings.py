from app.core.settings import Settings


def test_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("IAP_ALLOW_DEV_IDENTITY", raising=False)
    settings = Settings.from_env()
    assert settings.database_url == "postgresql+psycopg://iap:iap@127.0.0.1:5432/iap"
    assert settings.allow_dev_identity is False


def test_reads_database_and_dev_identity_settings(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db/app")
    monkeypatch.setenv("IAP_ALLOW_DEV_IDENTITY", "true")
    settings = Settings.from_env()
    assert settings.database_url.endswith("@db/app")
    assert settings.allow_dev_identity is True
