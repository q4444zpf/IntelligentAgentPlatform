from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import SessionFactory
from app.db.platform_models import McpClientRecord


class McpConcurrentUpdateError(Exception):
    pass


class McpStore:
    def __init__(self, session_factory: sessionmaker[Session] | None = None):
        self.session_factory = session_factory or SessionFactory

    @staticmethod
    def _decode(row: McpClientRecord | None) -> dict[str, Any] | None:
        if row is None:
            return None
        config = dict(row.config)
        config.setdefault("credential_id", row.credential_id)
        return {
            "key": row.client_key,
            "client_id": row.client_id,
            **config,
            "tool_records": row.tool_records,
            "tools": row.whitelist,
            "last_synced_at": row.last_synced_at,
            "version": row.version,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def list(self) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            rows = session.scalars(select(McpClientRecord).order_by(McpClientRecord.created_at, McpClientRecord.client_key))
            return [self._decode(row) for row in rows]

    def get(self, key: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            return self._decode(session.get(McpClientRecord, key))

    def create(self, key: str, config: dict[str, Any]) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            return self.create_in_session(session, key, config)

    def create_in_session(self, session: Session, key: str, config: dict[str, Any]) -> dict[str, Any]:
        row = McpClientRecord(client_key=key, client_id=key, credential_id=config.get("credential_id"), config=config, tool_records=[])
        session.add(row)
        session.flush()
        session.refresh(row)
        return self._decode(row)

    def update_config(self, key: str, config: dict[str, Any], expected_version: int | None = None) -> dict[str, Any] | None:
        return self._update(key, expected_version=expected_version, config=config)

    def update_tools(self, key: str, tools: list[dict[str, Any]], synced_at: str, expected_version: int | None = None) -> dict[str, Any] | None:
        return self._update(
            key,
            expected_version=expected_version,
            tool_records=tools,
            last_synced_at=synced_at,
        )

    def update_in_session(
        self,
        session: Session,
        key: str,
        *,
        expected_version: int,
        **values: Any,
    ) -> dict[str, Any] | None:
        result = session.execute(
            update(McpClientRecord)
            .where(
                McpClientRecord.client_key == key,
                McpClientRecord.version == expected_version,
            )
            .values(**values, version=expected_version + 1)
        )
        if result.rowcount != 1:
            if session.get(McpClientRecord, key) is None:
                return None
            raise McpConcurrentUpdateError(
                "MCP client changed concurrently; retry the request"
            )
        session.expire_all()
        return self._decode(session.get(McpClientRecord, key))

    def update_whitelist(self, key: str, tools: list[str] | None, expected_version: int | None = None) -> dict[str, Any] | None:
        return self._update(key, expected_version=expected_version, whitelist=tools)

    def _update(self, key: str, *, expected_version: int | None, **values: Any) -> dict[str, Any] | None:
        with self.session_factory.begin() as session:
            if expected_version is None:
                row = session.get(McpClientRecord, key)
                if row is None:
                    return None
                expected_version = row.version
            if "config" in values:
                values["credential_id"] = values["config"].get("credential_id")
            return self.update_in_session(
                session,
                key,
                expected_version=expected_version,
                **values,
            )
    def delete(self, key: str) -> bool:
        with self.session_factory.begin() as session:
            row = session.get(McpClientRecord, key)
            return self.delete_in_session(session, key)

    def delete_in_session(self, session: Session, key: str) -> bool:
        row = session.get(McpClientRecord, key)
        if row is None:
            return False
        session.delete(row)
        session.flush()
        return True
