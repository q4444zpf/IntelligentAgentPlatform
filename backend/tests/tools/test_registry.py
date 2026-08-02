import pytest
from sqlalchemy import create_engine
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
