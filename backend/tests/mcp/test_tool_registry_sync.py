import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.request_context import RequestContext
from app.db.base import Base
from app.db.platform_models import RegisteredToolRecord
from app.mcp.schemas import McpClientCreate
from app.mcp.service import McpService
from app.mcp.store import McpStore
from app.mcp.tool_registry import build_mcp_tool_id
from app.tools.service import TOOL_ID_PATTERN
from app.tools.store import ToolStore


@pytest.fixture
def registry_service(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'mcp-registry.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "tools-sync",
                "result": {
                    "tools": [
                        {
                            "name": "query_reservoir_level",
                            "description": "查询水位",
                            "inputSchema": {"type": "object"},
                        },
                        {
                            "name": "dispatch_gate",
                            "description": "调度闸门",
                            "inputSchema": {"type": "object"},
                        },
                    ]
                },
            },
        )

    service = McpService(
        McpStore(factory),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        tool_store=ToolStore(factory),
    )
    return service, factory


def test_build_mcp_tool_id_is_stable_valid_and_bounded():
    first = build_mcp_tool_id("water-data", "查询 水位/实时值")
    second = build_mcp_tool_id("water-data", "查询 水位/实时值")

    assert first == second
    assert first.startswith("mcp.water_data.")
    assert len(first) <= 128
    assert TOOL_ID_PATTERN.fullmatch(first)


def test_different_remote_names_do_not_collide():
    assert build_mcp_tool_id("water", "a-b") != build_mcp_tool_id("water", "a b")


def test_sync_registers_mcp_tools_as_available_unpublished(registry_service):
    service, factory = registry_service
    context = RequestContext(
        unit_id="unit-1",
        project_id="project-1",
        user_id="admin",
        roles=frozenset({"unit_admin"}),
    )
    request = McpClientCreate.model_validate(
        {
            "key": "water-data",
            "name": "水情 MCP",
            "transport": "streamable_http",
            "url": "https://water.example.com/mcp",
            "headers": {},
            "enabled": True,
        }
    )
    with factory() as session:
        service.create(
            request,
            context=context,
            session=session,
            request_id="create-water",
        )
    with factory() as session:
        service.sync_tools(
            "water-data",
            context=context,
            session=session,
            request_id="sync-water",
        )

    tools = [item for item in ToolStore(factory).list() if item["source"] == "mcp"]
    assert len(tools) == 2
    assert all(item["published"] is False for item in tools)
    assert all(item["enabled"] is True for item in tools)
    assert all(item["source_available"] is True for item in tools)
    assert {item["source_resource_id"] for item in tools} == {"water-data"}


def test_sync_defaults_missing_input_schema_to_object(registry_service):
    service, factory = registry_service
    service.http_client = httpx.Client(transport=httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={"result": {"tools": [{"name": "query_reservoir_level"}]}},
        )
    ))
    context = RequestContext(
        unit_id="unit-1",
        project_id="project-1",
        user_id="admin",
        roles=frozenset({"unit_admin"}),
    )
    request = McpClientCreate.model_validate({
        "key": "water-data",
        "name": "水情 MCP",
        "transport": "streamable_http",
        "url": "https://water.example.com/mcp",
        "headers": {},
        "enabled": True,
    })
    with factory() as session:
        service.create(request, context=context, session=session, request_id="create-water")
    with factory() as session:
        service.sync_tools("water-data", context=context, session=session, request_id="sync-water")

    tool = next(item for item in ToolStore(factory).list() if item["source"] == "mcp")
    assert tool["input_schema"] == {"type": "object"}


def test_resync_preserves_administrator_governance_fields(registry_service):
    service, factory = registry_service
    context = RequestContext(
        unit_id="unit-1",
        project_id="project-1",
        user_id="admin",
        roles=frozenset({"unit_admin"}),
    )
    request = McpClientCreate.model_validate(
        {
            "key": "water-data",
            "name": "水情 MCP",
            "transport": "streamable_http",
            "url": "https://water.example.com/mcp",
            "headers": {},
            "enabled": True,
        }
    )
    with factory() as session:
        service.create(request, context=context, session=session, request_id="create-water")
    with factory() as session:
        service.sync_tools("water-data", context=context, session=session, request_id="sync-1")

    tool_id = build_mcp_tool_id("water-data", "query_reservoir_level")
    with factory.begin() as session:
        row = session.get(RegisteredToolRecord, tool_id)
        row.risk_level = "high"
        row.requires_approval = True
        row.published = True
        row.enabled = False

    with factory() as session:
        service.sync_tools("water-data", context=context, session=session, request_id="sync-2")

    tool = ToolStore(factory).get(tool_id)
    assert tool["risk_level"] == "high"
    assert tool["requires_approval"] is True
    assert tool["published"] is True
    assert tool["enabled"] is False
    assert tool["source_available"] is True
