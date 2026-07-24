import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.mcp.router import create_router
from app.mcp.service import McpService
from app.mcp.store import McpStore


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

    service = McpService(
        McpStore(tmp_path / "mcp.db"),
        http_client=httpx.Client(transport=httpx.MockTransport(remote_handler)),
    )
    app = FastAPI()
    app.include_router(create_router(service), prefix="/api/mcp")
    with TestClient(app) as test_client:
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
