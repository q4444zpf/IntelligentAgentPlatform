from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.platform_models import McpToolRecord
from app.mcp.discovery_service import McpDiscoveryService
from app.mcp.tool_registry import McpToolRegistrySynchronizer
from app.tools.store import ToolStore


def _service(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'discovery.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return McpDiscoveryService(factory, McpToolRegistrySynchronizer(ToolStore(factory))), factory


def test_discovery_marks_added_changed_removed_and_unchanged_tools(tmp_path):
    discovery, factory = _service(tmp_path)
    first = [{"name": "one", "description": "v1", "input_schema": {"type": "object"}}, {"name": "gone"}]
    with factory.begin() as session:
        result = discovery.sync(session, client_id="client-1", client_key="water", tools=first)
    assert result.added == {"one", "gone"}
    assert result.changed == set()

    with factory.begin() as session:
        existing = session.scalar(select(McpToolRecord).where(McpToolRecord.id == "client-1:one"))
        existing.review_status = "published"

    with factory.begin() as session:
        result = discovery.sync(session, client_id="client-1", client_key="water", tools=[{"name": "one", "description": "v2"}, {"name": "new"}])

    assert result.added == {"new"}
    assert result.changed == {"one"}
    assert result.removed == {"gone"}
    with factory() as session:
        rows = {row.name: row for row in session.scalars(select(McpToolRecord))}
    assert rows["one"].review_status == "unpublished"
    assert rows["gone"].source_available is False
    assert rows["new"].review_status == "unpublished"


def test_discovery_normalizes_missing_schema(tmp_path):
    discovery, factory = _service(tmp_path)
    with factory.begin() as session:
        discovery.sync(session, client_id="client-1", client_key="water", tools=[{"name": "one"}])
    with factory() as session:
        row = session.get(McpToolRecord, "client-1:one")
    assert row.input_schema == {"type": "object"}
