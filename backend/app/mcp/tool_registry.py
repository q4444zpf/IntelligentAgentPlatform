from __future__ import annotations

import hashlib
import re

from sqlalchemy.orm import Session

from app.tools.store import ToolStore


_NON_IDENTIFIER = re.compile(r"[^a-z0-9_]+")
_REPEATED_UNDERSCORE = re.compile(r"_+")


def _slug(value: str, *, fallback: str) -> str:
    normalized = _NON_IDENTIFIER.sub("_", value.strip().lower())
    normalized = _REPEATED_UNDERSCORE.sub("_", normalized).strip("_")
    if not normalized or not normalized[0].isalpha():
        normalized = f"{fallback}_{normalized}".rstrip("_")
    return normalized


def build_mcp_tool_id(client_key: str, remote_tool_name: str) -> str:
    digest = hashlib.sha256(
        f"{client_key}\0{remote_tool_name}".encode("utf-8")
    ).hexdigest()[:8]
    client_slug = _slug(client_key, fallback="client")[:40].rstrip("_")
    tool_slug = _slug(remote_tool_name, fallback="tool")[:60].rstrip("_")
    return f"mcp.{client_slug}.{tool_slug}_{digest}"


class McpToolRegistrySynchronizer:
    def __init__(self, tool_store: ToolStore):
        self.tool_store = tool_store

    def sync(
        self,
        session: Session,
        client_key: str,
        remote_tools: list[dict],
    ) -> list[str]:
        tool_ids = []
        for remote_tool in remote_tools:
            remote_name = remote_tool["name"]
            tool_id = build_mcp_tool_id(client_key, remote_name)
            self.tool_store.upsert_mcp_in_session(
                session,
                {
                    "tool_id": tool_id,
                    "version": "1.0.0",
                    "name": remote_name,
                    "description": remote_tool.get("description", ""),
                    "source": "mcp",
                    "risk_level": "medium",
                    "input_schema": remote_tool.get("input_schema", {"type": "object"}),
                    "output_schema": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                    "requires_approval": False,
                    "published": False,
                    "enabled": True,
                    "source_resource_id": client_key,
                    "source_capability_id": remote_name,
                    "source_available": True,
                },
            )
            tool_ids.append(tool_id)
        return tool_ids

    def apply_client_state(self, session: Session, client_record: dict) -> None:
        discovered = {tool["name"] for tool in client_record["tool_records"]}
        whitelist = client_record["tools"]
        available = discovered if whitelist is None else discovered & set(whitelist)
        self.tool_store.update_mcp_source_state_in_session(
            session,
            client_record["key"],
            available,
            client_enabled=client_record["enabled"],
        )

    def retire_client(self, session: Session, client_key: str) -> None:
        self.tool_store.update_mcp_source_state_in_session(
            session,
            client_key,
            set(),
            client_enabled=False,
        )
