from sqlalchemy import DateTime, String

from app.db.base import Base


def test_audit_event_model_has_complete_schema_and_defaults():
    assert "audit_events" in Base.metadata.tables
    table = Base.metadata.tables["audit_events"]
    assert set(table.columns.keys()) == {
        "id", "unit_id", "project_id", "user_id", "actor_role", "category",
        "source", "action", "status", "risk_level", "trace_id", "run_id",
        "parent_event_id", "resource_type", "resource_id", "resource_name",
        "summary", "metadata_json", "error_code", "duration_ms",
        "idempotency_key", "occurred_at", "created_at",
    }
    assert isinstance(table.c.id.type, String) and table.c.id.type.length == 36
    assert table.c.id.primary_key and table.c.id.default is not None
    assert table.c.unit_id.nullable is False
    assert table.c.summary.nullable is False and table.c.summary.default is not None
    assert table.c.metadata_json.nullable is False
    assert table.c.metadata_json.default is not None
    assert isinstance(table.c.occurred_at.type, DateTime)
    assert table.c.occurred_at.type.timezone is True
    assert table.c.occurred_at.nullable is False
    assert table.c.created_at.type.timezone is True
    assert table.c.created_at.server_default is not None
    assert "updated_at" not in table.c
    assert {column.name for column in table.c if column.nullable} == {
        "project_id", "user_id", "actor_role", "trace_id", "run_id",
        "parent_event_id", "resource_type", "resource_id", "resource_name",
        "error_code", "duration_ms",
    }
    assert not table.c.run_id.foreign_keys
    assert not table.c.parent_event_id.foreign_keys


def test_audit_event_model_declares_uniqueness_and_query_indexes():
    assert "audit_events" in Base.metadata.tables
    table = Base.metadata.tables["audit_events"]
    assert "uq_audit_idempotency_key" in {
        constraint.name for constraint in table.constraints
    }
    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in table.indexes
    }
    assert indexes == {
        "ix_audit_unit_time": ("unit_id", "occurred_at", "id"),
        "ix_audit_project_time": ("unit_id", "project_id", "occurred_at", "id"),
        "ix_audit_user_time": ("unit_id", "project_id", "user_id", "occurred_at", "id"),
        "ix_audit_trace_time": ("trace_id", "occurred_at", "id"),
        "ix_audit_run_time": ("run_id", "occurred_at", "id"),
        "ix_audit_source_action_status": ("source", "action", "status"),
    }
