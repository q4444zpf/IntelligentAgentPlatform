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


def test_admin_can_publish_available_mcp_tool(tool_store):
    store, factory = tool_store
    definition = {
        "tool_id": "mcp.water.query_level_abcd1234",
        "version": "1.0.0",
        "name": "查询水位",
        "description": "读取水位",
        "source": "mcp",
        "risk_level": "medium",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "source_resource_id": "water",
        "source_capability_id": "query_level",
        "source_available": True,
        "requires_approval": False,
        "published": False,
        "enabled": True,
    }
    with factory.begin() as session:
        store.upsert_mcp_in_session(session, definition)
    service = ToolService(store)
    with factory() as session:
        result = service.set_published(
            definition["tool_id"],
            True,
            context=None,
            session=session,
        )
    assert result.published is True


def test_cannot_publish_source_unavailable_mcp_tool(tool_store):
    store, factory = tool_store
    definition = {
        "tool_id": "mcp.water.query_level_abcd1234",
        "version": "1.0.0",
        "name": "查询水位",
        "description": "读取水位",
        "source": "mcp",
        "risk_level": "medium",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "source_resource_id": "water",
        "source_capability_id": "query_level",
        "source_available": False,
        "requires_approval": False,
        "published": False,
        "enabled": True,
    }
    with factory.begin() as session:
        store.upsert_mcp_in_session(session, definition)
    with pytest.raises(ToolValidationError, match="source is unavailable"):
        with factory() as session:
            ToolService(store).set_published(
                definition["tool_id"], True, context=None, session=session
            )


def test_publish_without_current_project_records_unit_audit(tool_store):
    from sqlalchemy import select

    from app.audit.models import AuditEvent
    from app.core.request_context import RequestContext

    store, factory = tool_store
    service = ToolService(store)
    context = RequestContext(
        unit_id="unit-1",
        project_id="",
        user_id="admin",
        roles=frozenset({"unit_admin"}),
    )
    with factory() as session:
        service.set_published(
            "system.get_current_time",
            False,
            context=context,
            session=session,
            request_id="publish-unit-scope",
        )
        event = session.scalar(select(AuditEvent))

    assert event.authorization_scope == "unit"
    assert event.event_scope == "unit"
    assert event.project_id is None


def test_source_unavailable_tool_is_not_bindable(tool_store):
    store, factory = tool_store
    definition = {
        "tool_id": "mcp.water.query_level_abcd1234",
        "version": "1.0.0",
        "name": "查询水位",
        "description": "读取水位",
        "source": "mcp",
        "risk_level": "medium",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "source_resource_id": "water",
        "source_capability_id": "query_level",
        "source_available": False,
        "requires_approval": False,
        "published": True,
        "enabled": True,
    }
    with factory.begin() as session:
        store.upsert_mcp_in_session(session, definition)

    with pytest.raises(ToolValidationError, match="not available for binding"):
        ToolService(store).resolve_bindable([definition["tool_id"]])


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
