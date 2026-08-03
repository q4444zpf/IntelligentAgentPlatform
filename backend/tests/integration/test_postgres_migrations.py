import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, inspect

ALEMBIC_UPGRADE_COMMAND = (
    sys.executable,
    "-m",
    "alembic",
    "-c",
    "backend/alembic.ini",
    "upgrade",
    "head",
)


def test_upgrade_command_uses_backend_alembic_config():
    assert ALEMBIC_UPGRADE_COMMAND[3:5] == ("-c", "backend/alembic.ini")


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")
def test_upgrade_head_creates_conversation_tables():
    env = os.environ | {"DATABASE_URL": os.environ["TEST_DATABASE_URL"]}
    subprocess.run(ALEMBIC_UPGRADE_COMMAND, check=True, env=env)
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
        "audit_events",
    } <= tables
    conversation_columns = {
        column["name"]: column
        for column in inspector.get_columns("conversations")
    }
    assert conversation_columns["unit_id"]["nullable"] is False
    conversation_indexes = {
        index["name"]: tuple(index["column_names"])
        for index in inspector.get_indexes("conversations")
    }
    assert conversation_indexes["ix_conversations_unit_project_owner"] == ("unit_id", "project_id", "owner_id")
    invocation_columns = {
        column["name"]: column
        for column in inspector.get_columns("tool_invocations")
    }
    assert invocation_columns["error_code"]["nullable"] is True
    invocation_indexes = {
        index["name"] for index in inspector.get_indexes("tool_invocations")
    }
    assert "ix_tool_invocations_run_id" in invocation_indexes
    audit_columns = {
        column["name"]: column for column in inspector.get_columns("audit_events")
    }
    mcp_columns = {
        column["name"]: column for column in inspector.get_columns("mcp_clients")
    }
    assert mcp_columns["version"]["nullable"] is False
    assert audit_columns["unit_id"]["nullable"] is False
    assert audit_columns["metadata_json"]["nullable"] is False
    audit_constraints = inspector.get_unique_constraints("audit_events")
    idempotency_constraint = next(
        constraint for constraint in audit_constraints
        if constraint["name"] == "uq_audit_idempotency_key"
    )
    assert idempotency_constraint["column_names"] == ["idempotency_key"]
    audit_indexes = {
        index["name"]: tuple(index["column_names"])
        for index in inspector.get_indexes("audit_events")
    }
    expected_audit_indexes = {
        "ix_audit_unit_time": ("unit_id", "occurred_at", "id"),
        "ix_audit_project_time": ("unit_id", "project_id", "occurred_at", "id"),
        "ix_audit_user_time": (
            "unit_id", "project_id", "user_id", "occurred_at", "id"
        ),
        "ix_audit_trace_time": ("trace_id", "occurred_at", "id"),
        "ix_audit_run_time": ("run_id", "occurred_at", "id"),
        "ix_audit_source_action_status": ("source", "action", "status"),
    }
    assert {
        name: audit_indexes[name] for name in expected_audit_indexes
    } == expected_audit_indexes
