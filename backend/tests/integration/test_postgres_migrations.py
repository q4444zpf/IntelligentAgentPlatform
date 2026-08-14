import os
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

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
    engine = create_engine(env["DATABASE_URL"])
    inspector = inspect(engine)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20260814_18"
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
        "runtime_execution_snapshots",
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
    tool_columns = {
        column["name"]: column for column in inspector.get_columns("registered_tools")
    }
    assert mcp_columns["version"]["nullable"] is False
    assert tool_columns["source_resource_id"]["nullable"] is True
    assert tool_columns["source_capability_id"]["nullable"] is True
    assert tool_columns["source_available"]["nullable"] is False
    assert audit_columns["unit_id"]["nullable"] is True
    assert audit_columns["metadata_json"]["nullable"] is False
    assert audit_columns["actor_roles_json"]["nullable"] is False
    assert audit_columns["authorization_scope"]["nullable"] is False
    assert audit_columns["event_scope"]["nullable"] is False
    assert audit_columns["auth_method"]["nullable"] is True
    assert "actor_role" not in audit_columns
    run_columns = {
        column["name"]: column for column in inspector.get_columns("agent_runs")
    }
    assert run_columns["actor_roles_json"]["nullable"] is False
    assert "actor_role" not in run_columns
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
    assert "ck_audit_actor_role" not in audit_checks
    assert {
        "ck_audit_authorization_scope",
        "ck_audit_event_scope",
        "ck_audit_event_scope_ids",
        "ck_audit_category",
        "ck_audit_source",
    } <= audit_checks.keys()
    assert all(
        value in audit_checks["ck_audit_authorization_scope"]
        for value in ("platform", "unit", "project", "own", "emergency", "system")
    )
    assert all(
        value in audit_checks["ck_audit_event_scope"]
        for value in ("platform", "unit", "project")
    )
    assert "security" in audit_checks["ck_audit_category"]
    assert "auth" in audit_checks["ck_audit_source"]
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
    engine.dispose()


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")
def test_execution_snapshot_migration_creates_unique_run_index_and_cycles():
    database_url = os.environ["TEST_DATABASE_URL"]
    env = os.environ | {"DATABASE_URL": database_url}
    upgrade = (*ALEMBIC_UPGRADE_COMMAND[:-1], "20260814_18")
    downgrade = (*ALEMBIC_UPGRADE_COMMAND[:-2], "downgrade", "20260812_17")
    try:
        subprocess.run(upgrade, check=True, env=env)
        inspector = inspect(create_engine(database_url))
        assert "runtime_execution_snapshots" in inspector.get_table_names()
        indexes = {
            index["name"]: index
            for index in inspector.get_indexes("runtime_execution_snapshots")
        }
        assert indexes["ix_runtime_execution_snapshots_run_id"]["column_names"] == ["run_id"]
        assert indexes["ix_runtime_execution_snapshots_run_id"]["unique"] is True
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO runtime_execution_snapshots (
                    snapshot_id, run_id, digest, payload, created_at
                ) VALUES (
                    'immutable-snapshot', 'immutable-run', :digest,
                    CAST(:payload AS json), now()
                )
            """), {"digest": "a" * 64, "payload": '{"schema_version":"1"}'})
        with pytest.raises(DBAPIError):
            with engine.begin() as connection:
                connection.execute(text("""
                    UPDATE runtime_execution_snapshots
                    SET digest = :digest
                    WHERE snapshot_id = 'immutable-snapshot'
                """), {"digest": "b" * 64})
        with engine.begin() as connection:
            connection.execute(text("""
                DELETE FROM runtime_execution_snapshots
                WHERE snapshot_id = 'immutable-snapshot'
            """))

        subprocess.run(downgrade, check=True, env=env)
        assert "runtime_execution_snapshots" not in inspect(
            create_engine(database_url)
        ).get_table_names()

        subprocess.run(upgrade, check=True, env=env)
        assert "runtime_execution_snapshots" in inspect(
            create_engine(database_url)
        ).get_table_names()
    finally:
        subprocess.run(ALEMBIC_UPGRADE_COMMAND, check=True, env=env)


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")
def test_mcp_tool_registry_migration_downgrade_removes_source_columns():
    database_url = os.environ["TEST_DATABASE_URL"]
    env = os.environ | {"DATABASE_URL": database_url}
    subprocess.run(ALEMBIC_UPGRADE_COMMAND, check=True, env=env)
    downgrade = (*ALEMBIC_UPGRADE_COMMAND[:-2], "downgrade", "20260808_12")
    try:
        subprocess.run(downgrade, check=True, env=env)
        columns = {
            column["name"]
            for column in inspect(create_engine(database_url)).get_columns("registered_tools")
        }
        assert "source_resource_id" not in columns
        assert "source_capability_id" not in columns
        assert "source_available" not in columns
    finally:
        subprocess.run(ALEMBIC_UPGRADE_COMMAND, check=True, env=env)


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")
def test_audit_auth_contract_migration_cycles_legacy_snapshots_and_scope_constraints():
    database_url = os.environ["TEST_DATABASE_URL"]
    env = os.environ | {"DATABASE_URL": database_url}
    downgrade = (*ALEMBIC_UPGRADE_COMMAND[:-2], "downgrade", "20260804_09")
    subprocess.run(downgrade, check=True, env=env)
    engine = create_engine(database_url)
    legacy_ids = ("legacy-multi-role", "legacy-unknown-role")
    conversation_id = "legacy-role-conversation"
    message_id = "legacy-role-message"
    run_id = "legacy-role-run"
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
                "actor_role": "project_admin,user",
                "idempotency_key": legacy_ids[0],
            })
            connection.execute(insert, {
                "id": legacy_ids[1],
                "actor_role": "unknown",
                "idempotency_key": legacy_ids[1],
            })
            connection.execute(text("""
                INSERT INTO conversations (id, unit_id, project_id, owner_id, title)
                VALUES (:conversation_id, 'migration-unit', 'migration-project',
                        'migration-user', 'legacy roles')
            """), {"conversation_id": conversation_id})
            connection.execute(text("""
                INSERT INTO messages (id, conversation_id, sequence, role, content)
                VALUES (:message_id, :conversation_id, 1, 'user', 'legacy roles')
            """), {"message_id": message_id, "conversation_id": conversation_id})
            connection.execute(text("""
                INSERT INTO agent_runs (
                    id, conversation_id, trigger_message_id, actor_type, actor_id,
                    actor_role, status
                ) VALUES (
                    :run_id, :conversation_id, :message_id, 'agent', 'migration-agent',
                    'project_admin,user', 'completed'
                )
            """), {
                "run_id": run_id,
                "conversation_id": conversation_id,
                "message_id": message_id,
            })

        subprocess.run(ALEMBIC_UPGRADE_COMMAND, check=True, env=env)
        inspector = inspect(engine)
        assert "ck_audit_actor_role" not in {
            constraint["name"]
            for constraint in inspector.get_check_constraints("audit_events")
        }
        with engine.connect() as connection:
            snapshots = dict(connection.execute(
                text("SELECT id, actor_roles_json FROM audit_events WHERE id IN (:id1, :id2)"),
                {"id1": legacy_ids[0], "id2": legacy_ids[1]},
            ).all())
        assert snapshots == {
            legacy_ids[0]: ["project_admin", "user"],
            legacy_ids[1]: [],
        }
        with engine.begin() as connection:
            assert connection.execute(text(
                "SELECT actor_roles_json FROM agent_runs WHERE id=:run_id"
            ), {"run_id": run_id}).scalar_one() == ["project_admin", "user"]
            connection.execute(text("""
                UPDATE audit_events
                SET actor_roles_json='["unit_admin"]'::json
                WHERE id=:id
            """), {"id": legacy_ids[1]})
            connection.execute(text("""
                UPDATE agent_runs
                SET actor_roles_json='["unit_admin"]'::json
                WHERE id=:run_id
            """), {"run_id": run_id})

        valid_scope_insert = text("""
            INSERT INTO audit_events (
                id, unit_id, project_id, user_id, actor_roles_json,
                authorization_scope, event_scope, auth_method, category, source,
                action, status, risk_level, summary, metadata_json,
                idempotency_key, occurred_at
            ) VALUES (
                :id, :unit_id, :project_id, 'migration-user', '[]'::json,
                :authorization_scope, :event_scope, NULL, 'security', 'auth',
                'auth.login.succeeded', 'succeeded', 'medium', '', '{}'::json,
                :id, now()
            )
        """)
        scope_rows = (
            ("scope-platform", None, None, "platform", "platform"),
            ("scope-unit", "migration-unit", None, "unit", "unit"),
            ("scope-project", "migration-unit", "migration-project", "project", "project"),
        )
        with engine.begin() as connection:
            for row_id, unit_id, project_id, authorization_scope, event_scope in scope_rows:
                connection.execute(valid_scope_insert, {
                    "id": row_id,
                    "unit_id": unit_id,
                    "project_id": project_id,
                    "authorization_scope": authorization_scope,
                    "event_scope": event_scope,
                })
        invalid_rows = (
            ("invalid-platform", "migration-unit", None, "platform"),
            ("invalid-unit", None, None, "unit"),
            ("invalid-unit-project", "migration-unit", "migration-project", "unit"),
            ("invalid-project", "migration-unit", None, "project"),
        )
        for row_id, unit_id, project_id, event_scope in invalid_rows:
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(valid_scope_insert, {
                        "id": row_id,
                        "unit_id": unit_id,
                        "project_id": project_id,
                        "authorization_scope": event_scope,
                        "event_scope": event_scope,
                    })

        subprocess.run(downgrade, check=True, env=env)
        with engine.connect() as connection:
            assert dict(connection.execute(text(
                "SELECT id, actor_role FROM audit_events WHERE id IN (:id1, :id2)"
            ), {"id1": legacy_ids[0], "id2": legacy_ids[1]}).all()) == {
                legacy_ids[0]: "project_admin,user",
                legacy_ids[1]: "unknown",
            }
            assert connection.execute(text(
                "SELECT actor_role FROM agent_runs WHERE id=:run_id"
            ), {"run_id": run_id}).scalar_one() == "unknown"
            assert connection.execute(text("""
                SELECT category, source
                FROM audit_events
                WHERE id='scope-platform'
            """)).one() == ("management", "system")
    finally:
        subprocess.run(ALEMBIC_UPGRADE_COMMAND, check=True, env=env)
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM audit_events WHERE id IN (:id1, :id2, 'scope-platform', 'scope-unit', 'scope-project')"),
                {"id1": legacy_ids[0], "id2": legacy_ids[1]},
            )
            connection.execute(text("DELETE FROM agent_runs WHERE id=:run_id"), {"run_id": run_id})
            connection.execute(text("DELETE FROM messages WHERE id=:message_id"), {"message_id": message_id})
            connection.execute(text("DELETE FROM conversations WHERE id=:conversation_id"), {"conversation_id": conversation_id})
        engine.dispose()
