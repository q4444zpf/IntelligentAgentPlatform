from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any


class AgentStore:
    def __init__(self, path: str | Path | None = None):
        data_dir = Path(__file__).resolve().parents[2] / "data"
        self.path = Path(path or os.getenv("AGENT_DATABASE", data_dir / "agents.db"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agents (
                    agent_id TEXT PRIMARY KEY,
                    config_json TEXT NOT NULL,
                    workspace_dir TEXT NOT NULL,
                    pinned INTEGER NOT NULL DEFAULT 0,
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
            "id": row["agent_id"],
            **json.loads(row["config_json"]),
            "workspace_dir": row["workspace_dir"],
            "pinned": bool(row["pinned"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list(self) -> list[dict[str, Any]]:
        with self.lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM agents ORDER BY pinned DESC, created_at, agent_id"
            ).fetchall()
        return [self._decode(row) for row in rows]

    def get(self, agent_id: str) -> dict[str, Any] | None:
        with self.lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
        return self._decode(row)

    def create(self, agent_id: str, config: dict[str, Any], workspace_dir: str) -> dict[str, Any]:
        with self.lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO agents (agent_id, config_json, workspace_dir) VALUES (?, ?, ?)",
                (agent_id, json.dumps(config, ensure_ascii=False), workspace_dir),
            )
        return self.get(agent_id)

    def update(self, agent_id: str, config: dict[str, Any]) -> dict[str, Any] | None:
        with self.lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE agents SET config_json = ?, updated_at = CURRENT_TIMESTAMP WHERE agent_id = ?",
                (json.dumps(config, ensure_ascii=False), agent_id),
            )
        return self.get(agent_id) if cursor.rowcount else None

    def set_pinned(self, agent_id: str, pinned: bool) -> dict[str, Any] | None:
        with self.lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE agents SET pinned = ?, updated_at = CURRENT_TIMESTAMP WHERE agent_id = ?",
                (int(pinned), agent_id),
            )
        return self.get(agent_id) if cursor.rowcount else None

    def delete(self, agent_id: str) -> dict[str, Any] | None:
        record = self.get(agent_id)
        if not record:
            return None
        with self.lock, self._connect() as connection:
            connection.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
        return record
