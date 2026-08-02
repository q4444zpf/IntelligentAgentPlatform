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
    inspector = inspect(create_engine(env["DATABASE_URL"]))
    tables = set(inspector.get_table_names())
    assert {
        "conversations",
        "messages",
        "agent_runs",
        "run_events",
        "provider_configs",
        "custom_providers",
        "platform_settings",
        "managed_agents",
        "mcp_clients",
        "registered_tools",
        "tool_invocations",
    } <= tables
    invocation_indexes = {
        index["name"] for index in inspector.get_indexes("tool_invocations")
    }
    assert "ix_tool_invocations_run_id" in invocation_indexes
