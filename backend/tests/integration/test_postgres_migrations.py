import os
import subprocess

import pytest
from sqlalchemy import create_engine, inspect


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")
def test_upgrade_head_creates_conversation_tables():
    env = os.environ | {"DATABASE_URL": os.environ["TEST_DATABASE_URL"]}
    subprocess.run(
        ["python", "-m", "alembic", "upgrade", "head"], check=True, env=env
    )
    tables = set(inspect(create_engine(env["DATABASE_URL"])).get_table_names())
    assert {"conversations", "messages", "agent_runs", "run_events"} <= tables
