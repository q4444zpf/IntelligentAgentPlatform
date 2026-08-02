from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import SessionFactory
from app.db.platform_models import RegisteredToolRecord


class ToolStore:
    def __init__(self, session_factory: sessionmaker[Session] | None = None):
        self.session_factory = session_factory or SessionFactory

    @staticmethod
    def _decode(row: RegisteredToolRecord | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "tool_id": row.tool_id, "version": row.version, "name": row.name,
            "description": row.description, "source": row.source, "risk_level": row.risk_level,
            "input_schema": row.input_schema, "output_schema": row.output_schema,
            "requires_approval": row.requires_approval, "published": row.published,
            "enabled": row.enabled, "is_builtin": row.source == "builtin",
            "created_at": row.created_at, "updated_at": row.updated_at,
        }

    def list(self) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            rows = session.scalars(select(RegisteredToolRecord).order_by(RegisteredToolRecord.tool_id))
            return [self._decode(row) for row in rows]

    def get(self, tool_id: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            return self._decode(session.get(RegisteredToolRecord, tool_id))

    def upsert_builtin(self, definition: dict[str, Any]) -> dict[str, Any]:
        contract_fields = ("version", "name", "description", "source", "risk_level", "input_schema", "output_schema", "requires_approval", "published")
        with self.session_factory.begin() as session:
            row = session.get(RegisteredToolRecord, definition["tool_id"])
            if row is None:
                session.add(RegisteredToolRecord(**definition))
            else:
                for field in contract_fields:
                    setattr(row, field, definition[field])
        return self.get(definition["tool_id"])

    def set_enabled(self, tool_id: str, enabled: bool) -> dict[str, Any] | None:
        with self.session_factory.begin() as session:
            row = session.get(RegisteredToolRecord, tool_id)
            if row is None:
                return None
            row.enabled = enabled
        return self.get(tool_id)
