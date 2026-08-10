from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin

import httpx


class McpProtocolError(Exception):
    """A safe, user-facing MCP protocol failure."""


class McpProtocolClient:
    def __init__(self, http_client: httpx.Client | None = None, *, timeout: float = 15.0):
        self.http_client = http_client or httpx.Client(timeout=timeout, follow_redirects=False)

    def list_tools(self, url: str, transport: str, headers: dict[str, str]) -> list[dict[str, Any]]:
        try:
            if transport == "sse":
                return self._list_sse(url, headers)
            if transport == "streamable_http":
                return self._list_streamable_http(url, headers)
            raise McpProtocolError(f"Unsupported MCP transport: {transport}")
        except McpProtocolError:
            raise
        except (httpx.HTTPError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            raise McpProtocolError("remote MCP request failed") from error

    def _list_streamable_http(self, url: str, headers: dict[str, str]) -> list[dict[str, Any]]:
        request_headers = {**headers, "Accept": "application/json, text/event-stream"}
        session_id: str | None = None
        initialize = self._rpc(url, request_headers, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "IntelligentAgentPlatform", "version": "1.0"}}, 1)
        session_id = initialize[1]
        if session_id:
            request_headers["Mcp-Session-Id"] = session_id
        self._rpc(url, request_headers, "notifications/initialized", {}, None, allow_empty=True)
        return self._paginate(url, request_headers)

    def _list_sse(self, url: str, headers: dict[str, str]) -> list[dict[str, Any]]:
        response = self.http_client.get(url, headers={**headers, "Accept": "text/event-stream"})
        response.raise_for_status()
        endpoint = self._endpoint_from_sse(response.text)
        if not endpoint:
            raise McpProtocolError("remote MCP request failed")
        endpoint = urljoin(url, endpoint)
        request_headers = {**headers, "Accept": "application/json, text/event-stream"}
        self._rpc(endpoint, request_headers, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "IntelligentAgentPlatform", "version": "1.0"}}, 1)
        self._rpc(endpoint, request_headers, "notifications/initialized", {}, None, allow_empty=True)
        return self._paginate(endpoint, request_headers, sse_response=True)

    def _paginate(self, url: str, headers: dict[str, str], *, sse_response: bool = False) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params = {} if cursor is None else {"cursor": cursor}
            result, _ = self._rpc(url, headers, "tools/list", params, 2, sse_response=sse_response)
            page = result.get("result", {})
            tools.extend(item for item in page.get("tools", []) if isinstance(item, dict) and item.get("name"))
            cursor = page.get("nextCursor")
            if not cursor:
                return tools

    def _rpc(self, url: str, headers: dict[str, str], method: str, params: dict[str, Any], request_id: int | None, *, allow_empty: bool = False, sse_response: bool = False) -> tuple[dict[str, Any], str | None]:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if request_id is not None:
            payload["id"] = request_id
        response = self.http_client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            response.raise_for_status()
        session_id = response.headers.get("mcp-session-id")
        if allow_empty and not response.content:
            return {}, session_id
        data = self._decode_response(response, sse_response or "text/event-stream" in response.headers.get("content-type", ""))
        if "error" in data:
            raise McpProtocolError("remote MCP request failed")
        return data, session_id

    @staticmethod
    def _decode_response(response: httpx.Response, is_sse: bool) -> dict[str, Any]:
        if not is_sse:
            value = response.json()
            if not isinstance(value, dict):
                raise McpProtocolError("remote MCP request failed")
            return value
        for event in response.text.split("\n\n"):
            data_lines = [line[5:].strip() for line in event.splitlines() if line.startswith("data:")]
            if data_lines:
                value = json.loads("\n".join(data_lines))
                if isinstance(value, dict) and ("result" in value or "error" in value):
                    return value
        raise McpProtocolError("remote MCP request failed")

    @staticmethod
    def _endpoint_from_sse(text: str) -> str | None:
        for event in text.split("\n\n"):
            event_name = ""
            data = []
            for line in event.splitlines():
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
            if event_name == "endpoint" and data:
                return data[0]
        return None
