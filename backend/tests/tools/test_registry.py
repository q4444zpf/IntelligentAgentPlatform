import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.platform_models import RegisteredToolRecord
from app.tools.builtins import BUILTIN_TOOL_DEFINITIONS
from app.tools.service import ToolNotFoundError, ToolService, ToolValidationError
from app.tools.store import ToolStore


@pytest.fixture
def tool_store(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'tools.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return ToolStore(factory), factory


def test_service_initialization_is_idempotent_and_sorted(tool_store):
    store, factory = tool_store
    ToolService(store)
    service = ToolService(store)

    assert [tool.tool_id for tool in service.list()] == [
        "system.get_current_time",
        "system.get_runtime_context",
    ]
    with factory() as session:
        assert session.query(RegisteredToolRecord).count() == 2


def test_builtin_tools_expose_empty_source_mapping_and_available_source(tool_store):
    store, _ = tool_store
    tool = ToolService(store).get("system.get_current_time")

    assert tool.source_resource_id is None
    assert tool.source_capability_id is None
    assert tool.source_available is True


def test_initialization_repairs_builtin_contract_and_preserves_enabled(tool_store):
    store, factory = tool_store
    ToolService(store)
    with factory.begin() as session:
        row = session.get(RegisteredToolRecord, "system.get_current_time")
        row.name = "damaged"
        row.version = "0.0.0"
        row.input_schema = {"broken": True}
        row.enabled = False

    repaired = ToolService(store).get("system.get_current_time")
    expected = BUILTIN_TOOL_DEFINITIONS[0]
    assert repaired.name == expected["name"]
    assert repaired.version == expected["version"]
    assert repaired.input_schema == expected["input_schema"]
    assert repaired.enabled is False


def test_reads_and_toggles_registered_tool(tool_store):
    store, _ = tool_store
    service = ToolService(store)

    tool = service.get("system.get_runtime_context")
    assert tool.is_builtin is True
    assert tool.enabled is True
    assert service.toggle(tool.tool_id).enabled is False
    assert service.toggle(tool.tool_id).enabled is True


def test_rejects_invalid_or_unknown_tool_ids(tool_store):
    store, _ = tool_store
    service = ToolService(store)

    with pytest.raises(ToolValidationError):
        service.get("SYSTEM/unsafe")
    with pytest.raises(ToolNotFoundError):
        service.get("system.missing")


def test_builtin_tools_cannot_be_deleted(tool_store):
    store, _ = tool_store
    service = ToolService(store)

    with pytest.raises(ToolValidationError, match="cannot be deleted"):
        service.delete("system.get_current_time")

def test_builtin_upsert_uses_database_conflict_handling_and_preserves_enabled(tool_store):
    store, factory = tool_store
    definition = BUILTIN_TOOL_DEFINITIONS[0]
    store.upsert_builtin(definition)
    store.set_enabled(definition["tool_id"], False)
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(factory.kw["bind"], "before_cursor_execute", capture)
    try:
        repaired = store.upsert_builtin({**definition, "name": "修复后的名称"})
    finally:
        event.remove(factory.kw["bind"], "before_cursor_execute", capture)

    writes = [statement.upper() for statement in statements if statement.lstrip().upper().startswith("INSERT")]
    assert len(writes) == 1
    assert "ON CONFLICT" in writes[0]
    assert repaired["name"] == "修复后的名称"
    assert repaired["enabled"] is False


def test_toggle_is_one_atomic_database_update(tool_store):
    store, factory = tool_store
    service = ToolService(store)
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(factory.kw["bind"], "before_cursor_execute", capture)
    try:
        first = service.toggle("system.get_current_time")
        second = service.toggle("system.get_current_time")
    finally:
        event.remove(factory.kw["bind"], "before_cursor_execute", capture)

    updates = [statement.upper() for statement in statements if statement.lstrip().upper().startswith("UPDATE")]
    assert len(updates) == 2
    assert all("REGISTERED_TOOLS.ENABLED" in statement and "RETURNING" in statement for statement in updates)
    assert not any(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert first.enabled is False
    assert second.enabled is True
