import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, inspect, text


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="requires PostgreSQL",
)

ALEMBIC_UPGRADE_COMMAND = (
    sys.executable,
    "-m",
    "alembic",
    "-c",
    "backend/alembic.ini",
    "upgrade",
    "head",
)


def test_mcp_client_module_schema_has_unit_health_and_operation_tables():
    env = os.environ | {"DATABASE_URL": os.environ["TEST_DATABASE_URL"]}
    subprocess.run(ALEMBIC_UPGRADE_COMMAND, check=True, env=env)
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert {"mcp_clients", "mcp_project_grants", "mcp_tools", "mcp_health_checks", "mcp_operations"} <= tables

        columns = {column["name"] for column in inspector.get_columns("mcp_clients")}
        assert {
            "unit_id",
            "status",
            "credential_id",
            "health_status",
            "last_checked_at",
            "last_success_at",
            "failure_count",
            "health_lease_until",
        } <= columns

        with engine.connect() as connection:
            connection.execute(text("select 1 from mcp_project_grants limit 1"))
            connection.execute(text("select 1 from mcp_tools limit 1"))
            connection.execute(text("select 1 from mcp_health_checks limit 1"))
            connection.execute(text("select 1 from mcp_operations limit 1"))
    finally:
        engine.dispose()
