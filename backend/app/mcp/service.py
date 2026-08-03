from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import httpx
from sqlalchemy.exc import IntegrityError

from sqlalchemy.orm import Session

from app.audit.management import management_event_id, management_trace_id
from app.audit.recorder import AuditRecorder, AuditRecordRequest
from app.core.request_context import RequestContext
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
    def __init__(self, store: McpStore | None = None, http_client: httpx.Client | None = None, *, audit_recorder: AuditRecorder | None = None):
        self.store = store or McpStore()
        self.http_client = http_client or httpx.Client(timeout=15, follow_redirects=False)
        self.audit_recorder = audit_recorder or AuditRecorder()

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

    def _commit_management(
        self, context: RequestContext, session: Session, request_id: str | None,
        *, action: str, key: str, name: str, risk_level: str = "medium",
        metadata: dict | None = None,
    ) -> None:
        metadata = metadata or {}
        try:
            self.audit_recorder.record(session, AuditRecordRequest(
                unit_id=context.unit_id, project_id=context.project_id,
                user_id=context.user_id, actor_role=context.role,
                trace_id=management_trace_id(request_id), category="management", source="mcp", action=action,
                status="succeeded", risk_level=risk_level,
                resource_type="mcp_client", resource_id=key, resource_name=name,
                summary=f"MCP client {key} management operation succeeded",
                metadata=metadata, allowed_metadata_keys=frozenset(metadata),
                idempotency_key=f"management:{management_event_id(request_id)}:succeeded:{action}:{key}",
                occurred_at=datetime.now(UTC),
            ))
            session.commit()
        except Exception:
            session.rollback()
            raise

    def list(self) -> list[McpClientInfo]:
        return [self._info(record) for record in self.store.list()]

    def get(self, key: str) -> McpClientInfo:
        record = self.store.get(key)
        if not record:
            raise McpNotFoundError(key)
        return self._info(record)

    def create(self, request: McpClientCreate, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> McpClientInfo:
        if self.store.get(request.key):
            raise McpConflictError(f"MCP client '{request.key}' already exists")
        if context is None or session is None:
            try:
                record = self.store.create(request.key, request.model_dump(exclude={"key"}))
            except IntegrityError as error:
                raise McpConflictError(f"MCP client '{request.key}' already exists") from error
            return self._info(record)
        try:
            record = self.store.create_in_session(session, request.key, request.model_dump(exclude={"key"}))
            self.audit_recorder.record(session, AuditRecordRequest(
                unit_id=context.unit_id, project_id=context.project_id,
                user_id=context.user_id, actor_role=context.role,
                trace_id=management_trace_id(request_id), category="management", source="mcp", action="resource.created",
                status="succeeded", risk_level="medium", resource_type="mcp_client",
                resource_id=request.key, resource_name=request.name,
                summary=f"MCP client {request.key} was created",
                metadata={"transport": request.transport, "enabled": request.enabled},
                allowed_metadata_keys=frozenset({"transport", "enabled"}),
                idempotency_key=f"management:{management_event_id(request_id)}:succeeded:mcp.create:{request.key}",
                occurred_at=datetime.now(UTC),
            ))
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise McpConflictError(f"MCP client '{request.key}' already exists") from error
        except Exception:
            session.rollback()
            raise
        return self._info(record)

    def update(self, key: str, request: McpClientConfig, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> McpClientInfo:
        current = self.store.get(key)
        if not current:
            raise McpNotFoundError(key)
        config = request.model_dump()
        for header, value in config["headers"].items():
            if value == MASK and header in current["headers"]:
                config["headers"][header] = current["headers"][header]
        if context is None or session is None:
            record = self.store.update_config(key, config)
        else:
            record = self.store.update_in_session(session, key, config=config)
            self._commit_management(context, session, request_id, action="resource.updated", key=key, name=request.name, metadata={"transport": request.transport, "enabled": request.enabled})
        return self._info(record)

    def toggle(self, key: str, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> McpClientInfo:
        current = self.store.get(key)
        if not current:
            raise McpNotFoundError(key)
        config = {name: current[name] for name in McpClientConfig.model_fields}
        config["enabled"] = not config["enabled"]
        if context is None or session is None:
            record = self.store.update_config(key, config)
        else:
            record = self.store.update_in_session(session, key, config=config)
            self._commit_management(context, session, request_id, action="resource.enabled" if config["enabled"] else "resource.disabled", key=key, name=current["name"], metadata={"enabled": config["enabled"]})
        return self._info(record)

    def delete(self, key: str, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> None:
        current = self.store.get(key)
        if current is None:
            raise McpNotFoundError(key)
        if context is None or session is None:
            self.store.delete(key)
        else:
            self.store.delete_in_session(session, key)
            self._commit_management(context, session, request_id, action="resource.deleted", key=key, name=current["name"], risk_level="high")

    def list_tools(self, key: str) -> list[McpToolInfo]:
        record = self.store.get(key)
        if not record:
            raise McpNotFoundError(key)
        whitelist = record["tools"]
        return [
            McpToolInfo(**tool, enabled=whitelist is None or tool["name"] in whitelist)
            for tool in sorted(record["tool_records"], key=lambda item: item["name"])
        ]

    def sync_tools(self, key: str, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> list[McpToolInfo]:
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
        synced_at = datetime.now(UTC).isoformat()
        if context is None or session is None:
            self.store.update_tools(key, tools, synced_at)
        else:
            self.store.update_in_session(session, key, tool_records=tools, last_synced_at=synced_at)
            self._commit_management(context, session, request_id, action="resource.updated", key=key, name=record["name"], metadata={"tool_count": len(tools)})
        return self.list_tools(key)

    def update_whitelist(self, key: str, tools: list[str] | None, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> list[McpToolInfo]:
        record = self.store.get(key)
        if not record:
            raise McpNotFoundError(key)
        known = {tool["name"] for tool in record["tool_records"]}
        unknown = set(tools or []) - known
        if unknown:
            raise McpValidationError(f"Unknown MCP tools: {', '.join(sorted(unknown))}")
        if context is None or session is None:
            self.store.update_whitelist(key, tools)
        else:
            self.store.update_in_session(session, key, whitelist=tools)
            self._commit_management(context, session, request_id, action="resource.permission_changed", key=key, name=record["name"], risk_level="high", metadata={"tool_count": len(tools or [])})
        return self.list_tools(key)
