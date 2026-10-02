"""Run destructive migration exercises only in a disposable test schema."""

import os
from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


@contextmanager
def isolated_migration_database():
    raw = os.getenv("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("TEST_DATABASE_URL intentionally absent; dedicated PostgreSQL required")
    url = make_url(raw)
    if url.get_backend_name() != "postgresql":
        pytest.fail("TEST_DATABASE_URL must identify a dedicated PostgreSQL database")
    if "test" not in (url.database or "").lower():
        pytest.fail("Refusing migration tests: dedicated database name must contain 'test'")
    service_url = os.getenv("DATABASE_URL")
    if service_url:
        service = make_url(service_url)
        if (url.host, url.port, url.database) == (service.host, service.port, service.database):
            pytest.fail("TEST_DATABASE_URL must not identify the service/developer database")
    schema = "control_migration_" + uuid4().hex
    admin_engine = create_engine(url)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
    try:
        yield isolated_url.render_as_string(hide_password=False)
    finally:
        try:
            with admin_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        finally:
            admin_engine.dispose()
