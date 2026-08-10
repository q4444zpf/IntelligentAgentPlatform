from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import httpx
from sqlalchemy.exc import IntegrityError

from sqlalchemy.orm import Session
from sqlalchemy import delete, select

from app.audit.management import management_event_id, management_trace_id
from app.audit.recorder import AuditRecorder, AuditRecordRequest
from app.core.request_context import RequestContext
from app.tools.store import ToolStore
from app.db.platform_models import McpClientRecord, McpOperationRecord, McpProjectGrantRecord
from .protocol import McpProtocolClient, McpProtocolError
from .credential_resolver import McpCredentialResolver, CredentialNotFoundError, CredentialScopeError
from .discovery_service import McpDiscoveryService
from .schemas import McpClientConfig, McpClientCreate, McpClientInfo, McpToolInfo
from .store import McpConcurrentUpdateError, McpStore
from .tool_registry import McpToolRegistrySynchronizer
from .health_service import McpHealthService


MASK = "********"


def _management_audit_scope(context: RequestContext) -> tuple[str, str, str | None]:
    """MCP is unit-scoped; retain project scope only when a project is selected."""
    if context.project_id:
        return "project", "project", context.project_id
    return "unit", "unit", None


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
    def __init__(
        self,
        store: McpStore | None = None,
        http_client: httpx.Client | None = None,
        *,
        audit_recorder: AuditRecorder | None = None,
        tool_store: ToolStore | None = None,
        credential_resolver: McpCredentialResolver | None = None,
    ):
        self.store = store or McpStore()
        self.http_client = http_client or httpx.Client(timeout=15, follow_redirects=False)
        self.protocol_client = McpProtocolClient(self.http_client)
        self.audit_recorder = audit_recorder or AuditRecorder()
        self.tool_store = tool_store or ToolStore(self.store.session_factory)
        self.tool_registry = McpToolRegistrySynchronizer(self.tool_store)
        self.discovery_service = McpDiscoveryService(self.store.session_factory, self.tool_registry)
        self.credential_resolver = credential_resolver
        self.health_service = McpHealthService(self.store.session_factory)

    @staticmethod
    def _cas(operation):
        try:
            record = operation()
        except McpConcurrentUpdateError as error:
            raise McpConflictError(str(error)) from error
        if record is None:
            raise McpNotFoundError("MCP client")
        return record
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
            client_id=record.get("client_id") or record["key"],
            status=record.get("status", "active"),
            health_status=record.get("health_status", "not_checked"),
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
        authorization_scope, event_scope, project_id = _management_audit_scope(context)
        try:
            self.audit_recorder.record(session, AuditRecordRequest(
                unit_id=context.unit_id, project_id=project_id,
                user_id=context.user_id, actor_roles=context.role_codes,
                authorization_scope=authorization_scope, event_scope=event_scope,
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

    def list(self, *, context: RequestContext | None = None) -> list[McpClientInfo]:
        return [self._info(record) for record in self.store.list(unit_id=context.unit_id if context else None)]

    def _owned_record(self, key: str, context: RequestContext | None) -> dict:
        record = self.store.get(key)
        if record is None or (context is not None and record.get("unit_id") != context.unit_id):
            raise McpNotFoundError(key)
        return record

    def get(self, key: str, *, context: RequestContext | None = None) -> McpClientInfo:
        record = self._owned_record(key, context)
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
            record = self.store.create_in_session(session, request.key, request.model_dump(exclude={"key"}), unit_id=context.unit_id)
            authorization_scope, event_scope, project_id = _management_audit_scope(context)
            self.audit_recorder.record(session, AuditRecordRequest(
                unit_id=context.unit_id, project_id=project_id,
                user_id=context.user_id, actor_roles=context.role_codes,
                authorization_scope=authorization_scope, event_scope=event_scope,
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
        current = self._owned_record(key, context)
        config = request.model_dump()
        for header, value in config["headers"].items():
            if value == MASK and header in current["headers"]:
                config["headers"][header] = current["headers"][header]
        if context is None or session is None:
            record = self._cas(lambda: self.store.update_config(key, config, current["version"]))
        else:
            record = self._cas(lambda: self.store.update_in_session(session, key, expected_version=current["version"], config=config))
            self.tool_registry.apply_client_state(session, record)
            self._commit_management(context, session, request_id, action="resource.updated", key=key, name=request.name, metadata={"transport": request.transport, "enabled": request.enabled})
        return self._info(record)

    def toggle(self, key: str, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> McpClientInfo:
        current = self._owned_record(key, context)
        config = {name: current[name] for name in McpClientConfig.model_fields}
        config["enabled"] = not config["enabled"]
        if context is None or session is None:
            record = self._cas(lambda: self.store.update_config(key, config, current["version"]))
        else:
            record = self._cas(lambda: self.store.update_in_session(session, key, expected_version=current["version"], config=config))
            self.tool_registry.apply_client_state(session, record)
            self._commit_management(context, session, request_id, action="resource.enabled" if config["enabled"] else "resource.disabled", key=key, name=current["name"], metadata={"enabled": config["enabled"]})
        return self._info(record)

    def delete(self, key: str, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> None:
        current = self._owned_record(key, context)
        if context is None or session is None:
            self.store.delete(key)
        else:
            self.tool_registry.retire_client(session, key)
            self.store.delete_in_session(session, key)
            self._commit_management(context, session, request_id, action="resource.deleted", key=key, name=current["name"], risk_level="high")

    def list_tools(self, key: str, *, context: RequestContext | None = None) -> list[McpToolInfo]:
        record = self._owned_record(key, context)
        whitelist = record["tools"]
        return [
            McpToolInfo(**tool, enabled=whitelist is None or tool["name"] in whitelist)
            for tool in sorted(record["tool_records"], key=lambda item: item["name"])
        ]

    def sync_tools(self, key: str, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> list[McpToolInfo]:
        record = self._owned_record(key, context)
        if record["transport"] == "stdio":
            raise McpValidationError("stdio MCP must be synchronized by a sandbox worker")
        try:
            self.protocol_client.http_client = self.http_client
            headers = record["headers"]
            if record.get("credential_id"):
                if self.credential_resolver is None or context is None:
                    raise McpValidationError("credential is not available")
                try:
                    headers = self.credential_resolver.resolve(record["credential_id"], unit_id=context.unit_id)
                except (CredentialNotFoundError, CredentialScopeError) as error:
                    raise McpValidationError(str(error)) from error
            raw_tools = self.protocol_client.list_tools(record["url"], record["transport"], headers)
            tools = [
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("inputSchema") or tool.get("input_schema") or {"type": "object"},
                }
                for tool in raw_tools
                if isinstance(tool, dict) and tool.get("name")
            ]
        except McpProtocolError as error:
            raise McpValidationError(str(error)) from error
        synced_at = datetime.now(UTC).isoformat()
        if context is None or session is None:
            self._cas(lambda: self.store.update_tools(key, tools, synced_at, record["version"]))
        else:
            updated = self._cas(lambda: self.store.update_in_session(session, key, expected_version=record["version"], tool_records=tools, last_synced_at=synced_at))
            self.discovery_service.sync(
                session,
                client_id=record.get("client_id") or key,
                client_key=key,
                tools=tools,
            )
            self.tool_registry.apply_client_state(session, updated)
            self._commit_management(context, session, request_id, action="resource.updated", key=key, name=record["name"], metadata={"tool_count": len(tools)})
        return self.list_tools(key, context=context)

    def update_whitelist(self, key: str, tools: list[str] | None, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> list[McpToolInfo]:
        record = self._owned_record(key, context)
        known = {tool["name"] for tool in record["tool_records"]}
        unknown = set(tools or []) - known
        if unknown:
            raise McpValidationError(f"Unknown MCP tools: {', '.join(sorted(unknown))}")
        if context is None or session is None:
            self._cas(lambda: self.store.update_whitelist(key, tools, record["version"]))
        else:
            updated = self._cas(lambda: self.store.update_in_session(session, key, expected_version=record["version"], whitelist=tools))
            self.tool_registry.apply_client_state(session, updated)
            self._commit_management(context, session, request_id, action="resource.permission_changed", key=key, name=record["name"], risk_level="high", metadata={"tool_count": len(tools or [])})
        return self.list_tools(key, context=context)

    @staticmethod
    def _operation_info(row: McpOperationRecord) -> dict:
        return {"id": row.id, "client_id": row.client_id, "operation_type": row.operation_type, "status": row.status, "phase": row.phase, "result": row.result, "error_code": row.error_code, "error_message": row.error_message, "created_at": row.created_at, "completed_at": row.completed_at}

    def test_connection(self, key: str, *, context: RequestContext, session: Session) -> dict:
        record = self._owned_record(key, context)
        operation = self.health_service.start_operation(session, record.get("client_id") or key, "manual_test")
        try:
            self.protocol_client.http_client = self.http_client
            headers = record["headers"]
            if record.get("credential_id"):
                if self.credential_resolver is None:
                    raise McpValidationError("credential is not available")
                headers = self.credential_resolver.resolve(record["credential_id"], unit_id=context.unit_id)
            tools = self.protocol_client.list_tools(record["url"], record["transport"], headers)
            self.health_service.record_result(session, key, ok=True, phase="tools/list")
            self.health_service.update_operation(session, operation.id, status="succeeded", phase="tools/list", result={"tool_count": len(tools)})
        except Exception:
            self.health_service.record_result(session, key, ok=False, phase="initialize", error_code="CONNECTION_FAILED", error_message="remote MCP connection failed")
            self.health_service.update_operation(session, operation.id, status="failed", phase="initialize", error_code="CONNECTION_FAILED", error_message="remote MCP connection failed")
        session.commit()
        return self._operation_info(operation)

    def get_operation(self, operation_id: str, *, context: RequestContext, session: Session) -> dict:
        row = session.get(McpOperationRecord, operation_id)
        if row is None:
            raise McpNotFoundError(operation_id)
        client = session.scalar(select(McpClientRecord).where(McpClientRecord.client_id == row.client_id, McpClientRecord.unit_id == context.unit_id))
        if client is None:
            raise McpNotFoundError(operation_id)
        return self._operation_info(row)

    def health(self, key: str, *, context: RequestContext) -> dict:
        record = self._owned_record(key, context)
        return {name: record.get(name) for name in ("health_status", "last_checked_at", "last_success_at", "last_latency_ms", "failure_count", "last_error_code", "last_error_message")}

    def project_grants(self, key: str, *, context: RequestContext, session: Session) -> list[str]:
        record = self._owned_record(key, context)
        client_id = record.get("client_id") or key
        return sorted(session.scalars(select(McpProjectGrantRecord.project_id).where(McpProjectGrantRecord.client_id == client_id, McpProjectGrantRecord.unit_id == context.unit_id, McpProjectGrantRecord.status == "active")))

    def replace_project_grants(self, key: str, project_ids: list[str], *, context: RequestContext, session: Session) -> list[str]:
        record = self._owned_record(key, context)
        client_id = record.get("client_id") or key
        session.execute(delete(McpProjectGrantRecord).where(McpProjectGrantRecord.client_id == client_id, McpProjectGrantRecord.unit_id == context.unit_id))
        for project_id in sorted(set(project_ids)):
            session.add(McpProjectGrantRecord(id=f"{client_id}:{project_id}", client_id=client_id, unit_id=context.unit_id, project_id=project_id, status="active"))
        session.commit()
        return sorted(set(project_ids))

    def archive(self, key: str, *, context: RequestContext, session: Session) -> McpClientInfo:
        self._owned_record(key, context)
        self.tool_registry.retire_client(session, key)
        record = self.store.set_status_in_session(session, key, "archived")
        session.commit()
        return self._info(record)

    def restore(self, key: str, *, context: RequestContext, session: Session) -> McpClientInfo:
        self._owned_record(key, context)
        record = self.store.set_status_in_session(session, key, "active")
        self.tool_registry.apply_client_state(session, record)
        session.commit()
        return self._info(record)
