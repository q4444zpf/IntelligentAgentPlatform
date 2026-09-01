import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, inspect


pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")

ALEMBIC_UPGRADE_COMMAND = (
    sys.executable,
    "-m",
    "alembic",
    "-c",
    "backend/alembic.ini",
    "upgrade",
    "head",
)


def test_approval_workflow_schema_is_available():
    env = os.environ | {"DATABASE_URL": os.environ["TEST_DATABASE_URL"]}
    subprocess.run(ALEMBIC_UPGRADE_COMMAND, check=True, env=env)
    engine = create_engine(os.environ["TEST_DATABASE_URL"])
    try:
        inspector = inspect(engine)
        assert "approvals" in inspector.get_table_names()
        columns = {column["name"] for column in inspector.get_columns("approvals")}
        assert {"run_id", "invocation_id", "arguments_digest", "status", "expires_at", "decided_by"} <= columns
    finally:
        engine.dispose()
