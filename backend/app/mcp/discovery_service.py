from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.platform_models import McpToolRecord, RegisteredToolRecord
from app.mcp.tool_registry import McpToolRegistrySynchronizer, build_mcp_tool_id


@dataclass(frozen=True)
class McpDiscoveryDiff:
    added: set[str]
    changed: set[str]
    removed: set[str]
    unchanged: set[str]


class McpDiscoveryService:
    def __init__(self, session_factory: sessionmaker[Session], registry: McpToolRegistrySynchronizer):
        self.session_factory = session_factory
        self.registry = registry

    @staticmethod
    def _normalize(tool: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "input_schema": tool.get("input_schema") or tool.get("inputSchema") or {"type": "object"},
        }

    @classmethod
    def _hash(cls, tool: dict[str, Any]) -> str:
        normalized = cls._normalize(tool)
        return hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def sync(self, session: Session, *, client_id: str, client_key: str, tools: list[dict[str, Any]]) -> McpDiscoveryDiff:
        normalized = {tool["name"]: self._normalize(tool) for tool in tools if isinstance(tool, dict) and tool.get("name")}
        rows = {row.name: row for row in session.scalars(select(McpToolRecord).where(McpToolRecord.client_id == client_id))}
        added: set[str] = set()
        changed: set[str] = set()
        unchanged: set[str] = set()
        now = datetime.now(UTC)
        for name, tool in normalized.items():
            digest = self._hash(tool)
            row = rows.get(name)
            if row is None:
                row = McpToolRecord(id=f"{client_id}:{name}", client_id=client_id, name=name, description=tool["description"], input_schema=tool["input_schema"], schema_hash=digest, version=1, review_status="unpublished", source_available=True, first_seen_at=now, last_seen_at=now)
                session.add(row)
                added.add(name)
            elif row.schema_hash == digest and row.source_available:
                row.last_seen_at = now
                unchanged.add(name)
            else:
                row.description = tool["description"]
                row.input_schema = tool["input_schema"]
                row.schema_hash = digest
                row.version += 1
                row.review_status = "unpublished"
                row.source_available = True
                row.last_seen_at = now
                changed.add(name)
        removed = set(rows) - set(normalized)
        for name in removed:
            rows[name].source_available = False
            rows[name].review_status = "unpublished"

        registry_tools = list(normalized.values())
        self.registry.sync(session, client_key, registry_tools)
        for name in changed:
            registered = session.get(RegisteredToolRecord, build_mcp_tool_id(client_key, name))
            if registered is not None:
                registered.published = False
        self.registry.apply_client_state(session, {"key": client_key, "tool_records": registry_tools, "tools": None, "enabled": True})
        session.flush()
        return McpDiscoveryDiff(added=added, changed=changed, removed=removed, unchanged=unchanged)
