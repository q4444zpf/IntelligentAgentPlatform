import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.mcp.router import create_router
from app.mcp.schemas import McpClientConfig, McpClientCreate
from app.mcp.service import McpService
from app.mcp.store import McpStore
from app.db.base import Base

AUTH_HEADERS = {"X-Unit-ID": "unit-1", "X-User-ID": "u1", "X-Project-ID": "p1", "X-User-Role": "admin"}



@pytest.fixture
def client(tmp_path):
    def remote_handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://water.example.com/mcp"
        assert request.headers["authorization"] == "Bearer secret-token"
        payload = request.read().decode()
        assert '"method":"tools/list"' in payload
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "tools-sync",
                "result": {
                    "tools": [
                        {
                            "name": "query_reservoir_level",
                            "description": "查询水库水位",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"reservoir_id": {"type": "string"}},
                                "required": ["reservoir_id"],
                            },
                        },
                        {
                            "name": "dispatch_gate",
                            "description": "下发闸门调度指令",
                            "inputSchema": {"type": "object"},
                        },
                    ]
                },
            },
        )

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'mcp.db'}")
    Base.metadata.create_all(engine)
    service = McpService(
        McpStore(sessionmaker(bind=engine, expire_on_commit=False, class_=Session)),
        http_client=httpx.Client(transport=httpx.MockTransport(remote_handler)),
    )
    app = FastAPI()
    app.state.allow_dev_identity = True
    app.state.mcp_service = service
    app.include_router(create_router(service), prefix="/api/mcp")
    with TestClient(app, headers=AUTH_HEADERS) as test_client:
        yield test_client


def remote_payload(**overrides):
    payload = {
        "key": "water-data",
        "name": "水情数据 MCP",
        "description": "查询水库与河道实时数据",
        "transport": "streamable_http",
        "url": "https://water.example.com/mcp",
        "headers": {"Authorization": "Bearer secret-token", "X-Project": "demo"},
        "enabled": True,
    }
    payload.update(overrides)
    return payload


def test_validates_transport_specific_configuration(client):
    response = client.post(
        "/api/mcp",
        json=remote_payload(url=""),
    )
    assert response.status_code == 422
    assert "URL" in response.text

    response = client.post(
        "/api/mcp",
        json=remote_payload(key="local-tools", transport="stdio", url="", command=""),
    )
    assert response.status_code == 422
    assert "command" in response.text


def test_creates_lists_and_masks_sensitive_configuration(client):
    created = client.post("/api/mcp", json=remote_payload())
    assert created.status_code == 201
    body = created.json()
    assert body["key"] == "water-data"
    assert body["headers"] == {"Authorization": "********", "X-Project": "demo"}
    assert body["tool_count"] == 0

    listed = client.get("/api/mcp")
    assert listed.status_code == 200
    assert listed.json() == [body]

    duplicate = client.post("/api/mcp", json=remote_payload())
    assert duplicate.status_code == 409


def test_updates_masked_secrets_and_toggles_client(client):
    client.post("/api/mcp", json=remote_payload())

    updated = client.put(
        "/api/mcp/water-data",
        json={
            "name": "水利数据 MCP",
            "description": "更新后的说明",
            "transport": "streamable_http",
            "url": "https://water.example.com/mcp",
            "headers": {"Authorization": "********", "X-Project": "production"},
            "enabled": True,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "水利数据 MCP"
    assert updated.json()["headers"]["Authorization"] == "********"

    toggled = client.patch("/api/mcp/water-data/toggle")
    assert toggled.status_code == 200
    assert toggled.json()["enabled"] is False


def test_syncs_tools_and_updates_whitelist(client):
    client.post("/api/mcp", json=remote_payload())

    synced = client.post("/api/mcp/water-data/tools/sync")
    assert synced.status_code == 200
    assert [tool["name"] for tool in synced.json()] == [
        "dispatch_gate",
        "query_reservoir_level",
    ]
    assert all(tool["enabled"] for tool in synced.json())

    filtered = client.put(
        "/api/mcp/water-data/tools",
        json={"tools": ["query_reservoir_level"]},
    )
    assert filtered.status_code == 200
    states = {tool["name"]: tool["enabled"] for tool in filtered.json()}
    assert states == {"dispatch_gate": False, "query_reservoir_level": True}

    invalid = client.put(
        "/api/mcp/water-data/tools",
        json={"tools": ["missing_tool"]},
    )
    assert invalid.status_code == 422


def test_deletes_client(client):
    client.post("/api/mcp", json=remote_payload())
    deleted = client.delete("/api/mcp/water-data")
    assert deleted.status_code == 200
    assert client.get("/api/mcp/water-data").status_code == 404

def test_create_commits_mcp_and_redacted_management_audit_together(tmp_path):
    from sqlalchemy import select
    from app.audit.models import AuditEvent
    from app.core.request_context import RequestContext
    from app.mcp.schemas import McpClientCreate

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'mcp-audit.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    service = McpService(McpStore(factory))
    context = RequestContext(unit_id="unit-1", project_id="p1", user_id="u1")
    secret = "Bearer secret-token"
    with factory() as session:
        service.create(McpClientCreate.model_validate(remote_payload()), context=context, session=session, request_id="mcp-create-1")
        event = session.scalar(select(AuditEvent))
    assert event.action == "resource.created"
    assert event.source == "mcp"
    assert secret not in event.summary
    assert secret not in str(event.metadata_json)

def test_mcp_create_route_requires_request_context(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'mcp-auth.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app = FastAPI()
    app.state.allow_dev_identity = True
    app.include_router(create_router(McpService(McpStore(factory))), prefix="/api/mcp")
    with TestClient(app) as unauthenticated:
        response = unauthenticated.post("/api/mcp", json=remote_payload())
    assert response.status_code == 401

def test_missing_mcp_delete_records_failed_audit_in_fresh_transaction(client):
    from sqlalchemy import select
    from app.audit.models import AuditEvent

    response = client.delete(
        "/api/mcp/missing-client",
        headers={**AUTH_HEADERS, "X-Request-ID": "mcp-delete-missing-1"},
    )
    assert response.status_code == 404
    with client.app.state.mcp_service.store.session_factory() as session:
        event = session.scalar(select(AuditEvent))
    assert event.source == "mcp"
    assert event.action == "resource.deleted"
    assert event.status == "failed"
    assert event.error_code == "MCP_NOT_FOUND"
    assert event.resource_id == "missing-client"
    assert event.metadata_json == {}
    assert event.trace_id == "mcp-delete-missing-1"

def test_sync_rolls_back_tool_records_when_audit_recorder_fails(client):
    from app.audit.recorder import AuditRecorder

    class FailingRecorder(AuditRecorder):
        def record(self, session, request):
            raise RuntimeError("audit unavailable")

    created = client.post("/api/mcp", json=remote_payload())
    assert created.status_code == 201
    service = client.app.state.mcp_service
    service.audit_recorder = FailingRecorder()

    with pytest.raises(RuntimeError, match="audit unavailable"):
        client.post(
            "/api/mcp/water-data/tools/sync",
            headers={**AUTH_HEADERS, "X-Request-ID": "mcp-sync-audit-fail"},
        )

    reloaded = McpService(McpStore(service.store.session_factory)).get("water-data")
    assert reloaded.tool_count == 0

def test_invalid_mcp_body_records_failed_audit_without_request_secrets(client):
    from sqlalchemy import select
    from app.audit.models import AuditEvent

    secret = "Bearer validation-secret"
    payload = remote_payload()
    payload.pop("name")
    payload["headers"] = {"Authorization": secret}
    response = client.post(
        "/api/mcp",
        json=payload,
        headers={**AUTH_HEADERS, "X-Request-ID": "mcp-validation-1"},
    )
    assert response.status_code == 422
    with client.app.state.mcp_service.store.session_factory() as session:
        event = session.scalar(select(AuditEvent))
    assert event.source == "mcp"
    assert event.action == "resource.created"
    assert event.error_code == "REQUEST_VALIDATION"
    assert event.metadata_json == {}
    serialized = f"{event.summary} {event.metadata_json}"
    assert secret not in serialized
    assert event.trace_id == "mcp-validation-1"


def test_repeated_mcp_whitelist_without_request_id_records_each_mutation(client):
    from sqlalchemy import select
    from app.audit.models import AuditEvent

    assert client.post("/api/mcp", json=remote_payload()).status_code == 201
    assert client.put("/api/mcp/water-data/tools", json={"tools": []}).status_code == 200
    assert client.put("/api/mcp/water-data/tools", json={"tools": None}).status_code == 200
    factory = client.app.state.mcp_service.store.session_factory
    with factory() as session:
        events = list(session.scalars(select(AuditEvent).where(
            AuditEvent.source == "mcp",
            AuditEvent.action == "resource.permission_changed",
            AuditEvent.resource_id == "water-data",
        )))
    assert len(events) == 2
    assert len({event.idempotency_key for event in events}) == 2


def test_mcp_store_rejects_stale_config_update(tmp_path):
    from app.mcp.store import McpConcurrentUpdateError

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'mcp-cas.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    store = McpStore(factory)
    request = McpClientCreate.model_validate(remote_payload())
    created = store.create("water-data", request.model_dump(exclude={"key"}))
    first_config = {key: created[key] for key in McpClientConfig.model_fields}
    second_config = dict(first_config)
    first_config["name"] = "First winner"
    second_config["enabled"] = False
    assert store.update_config("water-data", first_config, created["version"])["name"] == "First winner"
    with pytest.raises(McpConcurrentUpdateError, match="concurrently"):
        store.update_config("water-data", second_config, created["version"])
    current = store.get("water-data")
    assert current["name"] == "First winner"
    assert current["enabled"] is True


def test_sync_rejects_stale_result_after_concurrent_config_update(client):
    from sqlalchemy import select
    from app.audit.models import AuditEvent
    from app.mcp.schemas import McpClientConfig, McpClientCreate

    assert client.post("/api/mcp", json=remote_payload()).status_code == 201
    service = client.app.state.mcp_service

    def concurrent_handler(_request: httpx.Request) -> httpx.Response:
        snapshot = service.store.get("water-data")
        config = {key: snapshot[key] for key in McpClientConfig.model_fields}
        config["enabled"] = False
        service.store.update_config("water-data", config, snapshot["version"])
        return httpx.Response(200, json={"result": {"tools": [{"name": "stale-tool"}]}})

    service.http_client = httpx.Client(transport=httpx.MockTransport(concurrent_handler))
    response = client.post(
        "/api/mcp/water-data/tools/sync",
        headers={"X-Request-ID": "sync-stale-correlation"},
    )
    assert response.status_code == 409
    current = service.store.get("water-data")
    assert current["enabled"] is False
    assert current["tool_records"] == []
    with service.store.session_factory() as session:
        event = session.scalar(select(AuditEvent).where(AuditEvent.status == "failed"))
    assert event.error_code == "MCP_CONFLICT"
    assert event.trace_id == "sync-stale-correlation"