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
        "audit_events",
    } <= tables
    conversation_columns = {
        column["name"]: column
        for column in inspector.get_columns("conversations")
    }
    assert conversation_columns["unit_id"]["nullable"] is False
    conversation_indexes = {
        index["name"] for index in inspector.get_indexes("conversations")
    }
    assert "ix_conversations_unit_project_owner" in conversation_indexes
    invocation_columns = {
        column["name"]: column
        for column in inspector.get_columns("tool_invocations")
    }
    assert invocation_columns["error_code"]["nullable"] is True
    invocation_indexes = {
        index["name"] for index in inspector.get_indexes("tool_invocations")
    }
    assert "ix_tool_invocations_run_id" in invocation_indexes
    audit_constraints = inspector.get_unique_constraints("audit_events")
    assert "uq_audit_idempotency_key" in {
        constraint["name"] for constraint in audit_constraints
    }
    audit_indexes = {
        index["name"] for index in inspector.get_indexes("audit_events")
    }
    assert {
        "ix_audit_unit_time",
        "ix_audit_project_time",
        "ix_audit_user_time",
        "ix_audit_trace_time",
        "ix_audit_run_time",
        "ix_audit_source_action_status",
    } <= audit_indexes
