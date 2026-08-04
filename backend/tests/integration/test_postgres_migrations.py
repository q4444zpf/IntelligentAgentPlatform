import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

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
    assert audit_columns["actor_role"]["nullable"] is False
    run_columns = {
        column["name"]: column for column in inspector.get_columns("agent_runs")
    }
    assert run_columns["actor_role"]["nullable"] is False
    audit_constraints = inspector.get_unique_constraints("audit_events")
    idempotency_constraint = next(
        constraint for constraint in audit_constraints
        if constraint["name"] == "uq_audit_idempotency_key"
    )
    assert idempotency_constraint["column_names"] == ["idempotency_key"]
    audit_checks = {
        constraint["name"]: constraint["sqltext"]
        for constraint in inspector.get_check_constraints("audit_events")
    }
    assert "ck_audit_actor_role" in audit_checks
    assert "project_admin,user" in audit_checks["ck_audit_actor_role"]
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


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")
def test_actor_role_migration_normalizes_legacy_values_and_enforces_constraint():
    database_url = os.environ["TEST_DATABASE_URL"]
    env = os.environ | {"DATABASE_URL": database_url}
    downgrade = (*ALEMBIC_UPGRADE_COMMAND[:-2], "downgrade", "20260804_07")
    subprocess.run(downgrade, check=True, env=env)
    engine = create_engine(database_url)
    legacy_ids = ("legacy-admin-role", "legacy-invalid-role")
    insert = text("""
        INSERT INTO audit_events (
            id, unit_id, project_id, user_id, actor_role, category, source,
            action, status, risk_level, summary, metadata_json,
            idempotency_key, occurred_at
        ) VALUES (
            :id, 'migration-unit', 'migration-project', 'migration-user',
            :actor_role, 'management', 'system', 'resource.updated',
            'succeeded', 'low', '', '{}'::json, :idempotency_key, now()
        )
    """)
    try:
        with engine.begin() as connection:
            connection.execute(insert, {
                "id": legacy_ids[0],
                "actor_role": "admin",
                "idempotency_key": legacy_ids[0],
            })
            connection.execute(insert, {
                "id": legacy_ids[1],
                "actor_role": "agent",
                "idempotency_key": legacy_ids[1],
            })

        subprocess.run(ALEMBIC_UPGRADE_COMMAND, check=True, env=env)
        inspector = inspect(engine)
        assert {
            column["name"]: column for column in inspector.get_columns("agent_runs")
        }["actor_role"]["nullable"] is False
        assert {
            column["name"]: column for column in inspector.get_columns("audit_events")
        }["actor_role"]["nullable"] is False
        assert "ck_audit_actor_role" in {
            constraint["name"]
            for constraint in inspector.get_check_constraints("audit_events")
        }
        with engine.connect() as connection:
            snapshots = dict(connection.execute(
                text("SELECT id, actor_role FROM audit_events WHERE id IN (:id1, :id2)"),
                {"id1": legacy_ids[0], "id2": legacy_ids[1]},
            ).all())
        assert snapshots == {
            legacy_ids[0]: "project_admin",
            legacy_ids[1]: "unknown",
        }
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE audit_events SET actor_role='admin' WHERE id=:id"),
                    {"id": legacy_ids[0]},
                )
    finally:
        subprocess.run(ALEMBIC_UPGRADE_COMMAND, check=True, env=env)
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM audit_events WHERE id IN (:id1, :id2)"),
                {"id1": legacy_ids[0], "id2": legacy_ids[1]},
            )
        engine.dispose()
