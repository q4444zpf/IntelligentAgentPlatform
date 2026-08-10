from typing import Any

from sqlalchemy import func, not_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import SessionFactory
from app.db.platform_models import RegisteredToolRecord


class ToolSourceUnavailableError(Exception):
    pass


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
            "source_resource_id": row.source_resource_id,
            "source_capability_id": row.source_capability_id,
            "source_available": row.source_available,
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

    def get_executable(self, tool_id: str) -> dict[str, Any] | None:
        item = self.get(tool_id)
        if (
            item is None
            or not item["published"]
            or not item["enabled"]
            or not item["source_available"]
        ):
            return None
        return item

    def upsert_builtin(self, definition: dict[str, Any]) -> dict[str, Any]:
        contract_fields = (
            "version", "name", "description", "source", "risk_level",
            "input_schema", "output_schema", "requires_approval", "published",
        )
        with self.session_factory.begin() as session:
            dialect = session.get_bind().dialect.name
            if dialect == "postgresql":
                statement = postgresql_insert(RegisteredToolRecord).values(**definition)
            elif dialect == "sqlite":
                statement = sqlite_insert(RegisteredToolRecord).values(**definition)
            else:
                raise RuntimeError(f"Unsupported tool registry dialect: {dialect}")
            statement = statement.on_conflict_do_update(
                index_elements=[RegisteredToolRecord.tool_id],
                set_={
                    **{field: getattr(statement.excluded, field) for field in contract_fields},
                    "updated_at": func.now(),
                },
            ).returning(RegisteredToolRecord)
            row = session.execute(statement).scalar_one()
            return self._decode(row)

    def upsert_mcp_in_session(
        self,
        session: Session,
        definition: dict[str, Any],
    ) -> dict[str, Any]:
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            statement = postgresql_insert(RegisteredToolRecord).values(**definition)
        elif dialect == "sqlite":
            statement = sqlite_insert(RegisteredToolRecord).values(**definition)
        else:
            raise RuntimeError(f"Unsupported tool registry dialect: {dialect}")
        statement = statement.on_conflict_do_update(
            index_elements=[RegisteredToolRecord.tool_id],
            set_={
                "version": statement.excluded.version,
                "name": statement.excluded.name,
                "description": statement.excluded.description,
                "source": statement.excluded.source,
                "input_schema": statement.excluded.input_schema,
                "output_schema": statement.excluded.output_schema,
                "source_resource_id": statement.excluded.source_resource_id,
                "source_capability_id": statement.excluded.source_capability_id,
                "source_available": True,
                "updated_at": func.now(),
            },
        ).returning(RegisteredToolRecord)
        row = session.execute(statement).scalar_one()
        session.flush()
        return self._decode(row)

    def update_mcp_source_state_in_session(
        self,
        session: Session,
        client_key: str,
        available_tool_names: set[str],
        *,
        client_enabled: bool,
    ) -> None:
        rows = session.scalars(
            select(RegisteredToolRecord).where(
                RegisteredToolRecord.source == "mcp",
                RegisteredToolRecord.source_resource_id == client_key,
            )
        )
        for row in rows:
            available = (
                client_enabled
                and row.source_capability_id in available_tool_names
            )
            row.source_available = available
            if not available:
                row.published = False
        session.flush()

    def set_enabled(self, tool_id: str, enabled: bool) -> dict[str, Any] | None:
        with self.session_factory.begin() as session:
            row = session.get(RegisteredToolRecord, tool_id)
            if row is None:
                return None
            row.enabled = enabled
        return self.get(tool_id)

    def toggle_in_session(self, session: Session, tool_id: str) -> dict[str, Any] | None:
        statement = (
            update(RegisteredToolRecord)
            .where(RegisteredToolRecord.tool_id == tool_id)
            .values(enabled=not_(RegisteredToolRecord.enabled), updated_at=func.now())
            .returning(RegisteredToolRecord)
        )
        row = session.execute(statement).scalar_one_or_none()
        session.flush()
        return self._decode(row)

    def toggle(self, tool_id: str) -> dict[str, Any] | None:
        with self.session_factory.begin() as session:
            return self.toggle_in_session(session, tool_id)

    def set_published_in_session(
        self,
        session: Session,
        tool_id: str,
        published: bool,
    ) -> dict[str, Any] | None:
        row = session.scalar(
            select(RegisteredToolRecord)
            .where(RegisteredToolRecord.tool_id == tool_id)
            .with_for_update()
        )
        if row is None:
            return None
        if published and not row.source_available:
            raise ToolSourceUnavailableError(tool_id)
        row.published = published
        session.flush()
        session.refresh(row)
        return self._decode(row)
