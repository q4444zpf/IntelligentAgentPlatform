from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import httpx

from .schemas import McpClientConfig, McpClientCreate, McpClientInfo, McpToolInfo
from .store import McpStore


MASK = "********"


class McpNotFoundError(Exception):
    pass


class McpConflictError(Exception):
    pass


class McpValidationError(Exception):
    pass


def _is_sensitive_header(name: str) -> bool:
    lowered = name.lower()
    return any(part in lowered for part in ("authorization", "api-key", "apikey", "token", "secret"))


class McpService:
    def __init__(self, store: McpStore | None = None, http_client: httpx.Client | None = None):
        self.store = store or McpStore()
        self.http_client = http_client or httpx.Client(timeout=15, follow_redirects=False)

    def _info(self, record: dict) -> McpClientInfo:
        whitelist = record["tools"]
        tools = record["tool_records"]
        config = {name: record[name] for name in McpClientConfig.model_fields}
        config["headers"] = {
            key: MASK if _is_sensitive_header(key) and value else value
            for key, value in config["headers"].items()
        }
        return McpClientInfo(
            key=record["key"],
            **config,
            tools=whitelist,
            tool_count=len(tools),
            enabled_tool_count=sum(whitelist is None or tool["name"] in whitelist for tool in tools),
            last_synced_at=record["last_synced_at"],
            created_at=record["created_at"],
            updated_at=record["updated_at"],
        )

    def list(self) -> list[McpClientInfo]:
        return [self._info(record) for record in self.store.list()]

    def get(self, key: str) -> McpClientInfo:
        record = self.store.get(key)
        if not record:
            raise McpNotFoundError(key)
        return self._info(record)

    def create(self, request: McpClientCreate) -> McpClientInfo:
        if self.store.get(request.key):
            raise McpConflictError(f"MCP client '{request.key}' already exists")
        try:
            record = self.store.create(request.key, request.model_dump(exclude={"key"}))
        except sqlite3.IntegrityError as error:
            raise McpConflictError(f"MCP client '{request.key}' already exists") from error
        return self._info(record)

    def update(self, key: str, request: McpClientConfig) -> McpClientInfo:
        current = self.store.get(key)
        if not current:
            raise McpNotFoundError(key)
        config = request.model_dump()
        for header, value in config["headers"].items():
            if value == MASK and header in current["headers"]:
                config["headers"][header] = current["headers"][header]
        record = self.store.update_config(key, config)
        return self._info(record)

    def toggle(self, key: str) -> McpClientInfo:
        current = self.store.get(key)
        if not current:
            raise McpNotFoundError(key)
        config = {name: current[name] for name in McpClientConfig.model_fields}
        config["enabled"] = not config["enabled"]
        return self._info(self.store.update_config(key, config))

    def delete(self, key: str) -> None:
        if not self.store.delete(key):
            raise McpNotFoundError(key)

    def list_tools(self, key: str) -> list[McpToolInfo]:
        record = self.store.get(key)
        if not record:
            raise McpNotFoundError(key)
        whitelist = record["tools"]
        return [
            McpToolInfo(**tool, enabled=whitelist is None or tool["name"] in whitelist)
            for tool in sorted(record["tool_records"], key=lambda item: item["name"])
        ]

    def sync_tools(self, key: str) -> list[McpToolInfo]:
        record = self.store.get(key)
        if not record:
            raise McpNotFoundError(key)
        if record["transport"] == "stdio":
            raise McpValidationError("stdio MCP must be synchronized by a sandbox worker")
        try:
            response = self.http_client.post(
                record["url"],
                headers={**record["headers"], "Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": "tools-sync", "method": "tools/list", "params": {}},
            )
            response.raise_for_status()
            payload = response.json()
            raw_tools = payload.get("result", {}).get("tools", [])
            tools = [
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("inputSchema", {}),
                }
                for tool in raw_tools
                if isinstance(tool, dict) and tool.get("name")
            ]
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise McpValidationError(f"Unable to synchronize MCP tools: {error}") from error
        self.store.update_tools(key, tools, datetime.now(UTC).isoformat())
        return self.list_tools(key)

    def update_whitelist(self, key: str, tools: list[str] | None) -> list[McpToolInfo]:
        record = self.store.get(key)
        if not record:
            raise McpNotFoundError(key)
        known = {tool["name"] for tool in record["tool_records"]}
        unknown = set(tools or []) - known
        if unknown:
            raise McpValidationError(f"Unknown MCP tools: {', '.join(sorted(unknown))}")
        self.store.update_whitelist(key, tools)
        return self.list_tools(key)
