from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any


class McpStore:
    def __init__(self, path: str | Path | None = None):
        data_dir = Path(__file__).resolve().parents[2] / "data"
        self.path = Path(path or os.getenv("MCP_DATABASE", data_dir / "mcp.db"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS mcp_clients (
                    client_key TEXT PRIMARY KEY,
                    config_json TEXT NOT NULL,
                    tools_json TEXT NOT NULL DEFAULT '[]',
                    whitelist_json TEXT,
                    last_synced_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "key": row["client_key"],
            **json.loads(row["config_json"]),
            "tool_records": json.loads(row["tools_json"]),
            "tools": json.loads(row["whitelist_json"]) if row["whitelist_json"] is not None else None,
            "last_synced_at": row["last_synced_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list(self) -> list[dict[str, Any]]:
        with self.lock, self._connect() as connection:
            rows = connection.execute("SELECT * FROM mcp_clients ORDER BY created_at, client_key").fetchall()
        return [self._decode(row) for row in rows]

    def get(self, key: str) -> dict[str, Any] | None:
        with self.lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM mcp_clients WHERE client_key = ?", (key,)).fetchone()
        return self._decode(row)

    def create(self, key: str, config: dict[str, Any]) -> dict[str, Any]:
        with self.lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO mcp_clients (client_key, config_json) VALUES (?, ?)",
                (key, json.dumps(config, ensure_ascii=False)),
            )
        return self.get(key)

    def update_config(self, key: str, config: dict[str, Any]) -> dict[str, Any] | None:
        with self.lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE mcp_clients SET config_json = ?, updated_at = CURRENT_TIMESTAMP WHERE client_key = ?",
                (json.dumps(config, ensure_ascii=False), key),
            )
        return self.get(key) if cursor.rowcount else None

    def update_tools(self, key: str, tools: list[dict[str, Any]], synced_at: str) -> dict[str, Any] | None:
        with self.lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE mcp_clients SET tools_json = ?, last_synced_at = ?, updated_at = CURRENT_TIMESTAMP WHERE client_key = ?",
                (json.dumps(tools, ensure_ascii=False), synced_at, key),
            )
        return self.get(key) if cursor.rowcount else None

    def update_whitelist(self, key: str, tools: list[str] | None) -> dict[str, Any] | None:
        value = json.dumps(tools, ensure_ascii=False) if tools is not None else None
        with self.lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE mcp_clients SET whitelist_json = ?, updated_at = CURRENT_TIMESTAMP WHERE client_key = ?",
                (value, key),
            )
        return self.get(key) if cursor.rowcount else None

    def delete(self, key: str) -> bool:
        with self.lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM mcp_clients WHERE client_key = ?", (key,))
        return cursor.rowcount > 0
