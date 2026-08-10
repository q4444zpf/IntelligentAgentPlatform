import json

import httpx
import pytest

from app.mcp.protocol import McpProtocolClient, McpProtocolError


def test_streamable_http_runs_initialize_notification_and_paginates_tools():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append((request.method, body, request.headers.get("mcp-session-id")))
        if body["method"] == "initialize":
            return httpx.Response(
                200,
                headers={"mcp-session-id": "session-1"},
                json={"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-03-26"}},
            )
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        if body["method"] == "tools/list" and body["params"].get("cursor") is None:
            return httpx.Response(200, json={"result": {"tools": [{"name": "one"}], "nextCursor": "next"}})
        return httpx.Response(200, json={"result": {"tools": [{"name": "two"}]}})

    client = McpProtocolClient(httpx.Client(transport=httpx.MockTransport(handler)))
    tools = client.list_tools("https://example.test/mcp", "streamable_http", {})

    assert [tool["name"] for tool in tools] == ["one", "two"]
    assert [call[1]["method"] for call in calls] == ["initialize", "notifications/initialized", "tools/list", "tools/list"]
    assert calls[2][2] == calls[3][2] == "session-1"


def test_sse_discovers_message_endpoint_and_parses_data_events():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.method == "GET":
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text="event: endpoint\ndata: /messages\n\n")
        body = json.loads(request.content)
        if body["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {}})
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text='data: {"result":{"tools":[{"name":"sse-tool"}]}}\n\n')

    client = McpProtocolClient(httpx.Client(transport=httpx.MockTransport(handler)))
    tools = client.list_tools("https://example.test/sse", "sse", {})

    assert tools == [{"name": "sse-tool"}]
    assert calls[0] == ("GET", "https://example.test/sse")
    assert calls[1][1] == "https://example.test/messages"


def test_protocol_errors_are_sanitized():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Bearer top-secret")

    client = McpProtocolClient(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(McpProtocolError, match="remote MCP request failed") as error:
        client.list_tools("https://example.test/mcp", "streamable_http", {"Authorization": "Bearer top-secret"})
    assert "top-secret" not in str(error.value)


def test_streamable_http_calls_remote_tool_after_initializing_session():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append((body, request.headers.get("mcp-session-id")))
        if body["method"] == "initialize":
            return httpx.Response(
                200,
                headers={"mcp-session-id": "session-2"},
                json={"jsonrpc": "2.0", "id": body["id"], "result": {}},
            )
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(200, json={"result": {"answer": "ok"}})

    client = McpProtocolClient(httpx.Client(transport=httpx.MockTransport(handler)))
    result = client.call_tool(
        "https://example.test/mcp", "streamable_http", {}, "read_wiki", {"repo": "github"}
    )

    assert result == {"answer": "ok"}
    assert [item[0]["method"] for item in calls] == [
        "initialize", "notifications/initialized", "tools/call"
    ]
    assert calls[-1][0]["params"] == {
        "name": "read_wiki", "arguments": {"repo": "github"}
    }
    assert calls[-1][1] == "session-2"
