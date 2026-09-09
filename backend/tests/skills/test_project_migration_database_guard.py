from contextlib import contextmanager

import pytest

from backend.tests.integration import (
    test_skill_project_permissions_postgres as migration,
)

DEDICATED_URL = (
    "postgresql+psycopg://test-user:test-secret@localhost/"
    "iap_skill_control_migration_20260908_a"
)


@pytest.fixture
def engine_calls(monkeypatch):
    calls = []

    class FakeEngine:
        database = "iap_skill_control_migration_20260908_a"

        @contextmanager
        def begin(self):
            calls.append("begin")
            yield self

        def scalar(self, statement):
            calls.append(str(statement))
            return self.database

        def execute(self, statement):
            calls.append(str(statement))

        def dispose(self):
            calls.append("dispose")

    engine = FakeEngine()

    def create_engine(url):
        calls.append("create_engine")
        return engine

    monkeypatch.setattr(migration, "create_engine", create_engine)
    return calls, engine


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://test-user:test-secret@localhost/iap",
        "postgresql+psycopg://localhost/iap_skill_control_test_20260908_a",
        "postgresql+psycopg://localhost/iap_skill_versions_test",
        "postgresql+psycopg://localhost/arbitrary",
        "postgresql+psycopg://localhost/prefix_iap_skill_control_migration_20260908_a",
        "postgresql+psycopg://localhost/iap_skill_control_migration_20260908_a_suffix",
        "postgresql+psycopg://localhost/",
        "sqlite:///iap_skill_control_migration_20260908_a",
        "mysql://localhost/iap_skill_control_migration_20260908_a",
        "not-a-database-url",
        "postgresql+psycopg://localhost:test-secret/iap_skill_control_migration_20260908_a",
        DEDICATED_URL + "?dbname=iap",
        DEDICATED_URL + "?database=iap",
        DEDICATED_URL + "?service=business",
        DEDICATED_URL + "?host=other-host",
        DEDICATED_URL + "?options=-c%20dbname%3Diap",
        DEDICATED_URL + "?dsn=dbname%3Diap",
        DEDICATED_URL + "?%64bname=iap",
        DEDICATED_URL + "?dbname=iap_skill_control_migration_20260908_a&dbname=iap",
    ],
    ids=[
        "business",
        "service",
        "prior-phase",
        "arbitrary",
        "prefix",
        "suffix",
        "no-database",
        "sqlite",
        "mysql",
        "malformed",
        "malformed-port",
        "dbname-override",
        "database-override",
        "service-override",
        "host-override",
        "options-override",
        "dsn-override",
        "encoded-override",
        "repeated-override",
    ],
)
def test_rejects_unsafe_migration_target_before_engine_or_sql(
    monkeypatch, engine_calls, url
):
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    calls, _ = engine_calls
    fixture = migration.empty_migration_database.__wrapped__()
    rejection = None
    try:
        next(fixture)
    except ValueError as error:
        rejection = str(error)
    finally:
        fixture.close()

    assert calls == []
    assert rejection is not None
    assert url not in rejection
    assert "test-secret" not in rejection


@pytest.mark.parametrize("value", [None, ""])
def test_missing_migration_configuration_skips_without_engine_or_sql(
    monkeypatch, engine_calls, value
):
    if value is None:
        monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("TEST_DATABASE_URL", value)
    calls, _ = engine_calls
    fixture = migration.empty_migration_database.__wrapped__()
    with pytest.raises(pytest.skip.Exception):
        next(fixture)
    assert calls == []


def test_checks_connected_database_before_resetting_dedicated_target(
    monkeypatch, engine_calls
):
    monkeypatch.setenv("TEST_DATABASE_URL", DEDICATED_URL)
    calls, _ = engine_calls
    fixture = migration.empty_migration_database.__wrapped__()
    next(fixture)
    fixture.close()
    assert calls == [
        "create_engine",
        "begin",
        "SELECT current_database()",
        "DROP SCHEMA public CASCADE",
        "CREATE SCHEMA public",
        "dispose",
    ]


def test_rejects_connected_database_mismatch_without_destructive_sql(
    monkeypatch, engine_calls
):
    monkeypatch.setenv("TEST_DATABASE_URL", DEDICATED_URL)
    calls, engine = engine_calls
    engine.database = "iap"
    fixture = migration.empty_migration_database.__wrapped__()
    with pytest.raises(ValueError):
        next(fixture)
    assert calls == ["create_engine", "begin", "SELECT current_database()", "dispose"]
