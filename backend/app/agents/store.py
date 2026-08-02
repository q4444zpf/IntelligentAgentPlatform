from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import SessionFactory
from app.db.platform_models import ManagedAgentRecord, PlatformSettingRecord


DEFAULT_SETTING_KEY = "default_agent"


class AgentConcurrentUpdateError(ValueError):
    pass


@dataclass(frozen=True)
class DefaultAgentPointer:
    agent_id: str | None
    version: int


class AgentStore:
    def __init__(self, session_factory: sessionmaker[Session] | None = None):
        self.session_factory = session_factory or SessionFactory

    @staticmethod
    def _decode(row: ManagedAgentRecord | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": row.agent_id,
            **row.config,
            "workspace_dir": row.workspace_dir,
            "pinned": row.pinned,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def list(self) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            rows = session.scalars(select(ManagedAgentRecord).order_by(ManagedAgentRecord.pinned.desc(), ManagedAgentRecord.created_at, ManagedAgentRecord.agent_id))
            return [self._decode(row) for row in rows]

    def get(self, agent_id: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            return self._decode(session.get(ManagedAgentRecord, agent_id))

    def get_default_id(self) -> DefaultAgentPointer:
        with self.session_factory() as session:
            row = session.get(PlatformSettingRecord, DEFAULT_SETTING_KEY)
            if row is None:
                return DefaultAgentPointer(agent_id=None, version=0)
            value = row.value if isinstance(row.value, dict) else {}
            agent_id = value.get("agent_id")
            is_platform_pointer = value.get("scope") == "platform"
            return DefaultAgentPointer(
                agent_id=(
                    agent_id
                    if is_platform_pointer and isinstance(agent_id, str)
                    else None
                ),
                version=row.version,
            )

    def set_default_id(
        self,
        agent_id: str,
        expected_version: int,
    ) -> DefaultAgentPointer:
        value = {"agent_id": agent_id, "scope": "platform"}
        try:
            with self.session_factory.begin() as session:
                if expected_version:
                    result = session.execute(
                        update(PlatformSettingRecord)
                        .where(
                            PlatformSettingRecord.setting_key == DEFAULT_SETTING_KEY,
                            PlatformSettingRecord.version == expected_version,
                        )
                        .values(value=value, version=expected_version + 1)
                    )
                    if result.rowcount != 1:
                        raise AgentConcurrentUpdateError(
                            "Default agent changed concurrently; retry the request"
                        )
                else:
                    session.add(
                        PlatformSettingRecord(
                            setting_key=DEFAULT_SETTING_KEY,
                            value=value,
                        )
                    )
        except IntegrityError as error:
            raise AgentConcurrentUpdateError(
                "Default agent changed concurrently; retry the request"
            ) from error
        return DefaultAgentPointer(
            agent_id=agent_id,
            version=expected_version + 1,
        )

    def create(self, agent_id: str, config: dict[str, Any], workspace_dir: str) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            session.add(ManagedAgentRecord(agent_id=agent_id, config=config, workspace_dir=workspace_dir, pinned=False))
        return self.get(agent_id)

    def update(self, agent_id: str, config: dict[str, Any]) -> dict[str, Any] | None:
        with self.session_factory.begin() as session:
            row = session.get(ManagedAgentRecord, agent_id)
            if row is None:
                return None
            row.config = config
        return self.get(agent_id)

    def set_pinned(self, agent_id: str, pinned: bool) -> dict[str, Any] | None:
        with self.session_factory.begin() as session:
            row = session.get(ManagedAgentRecord, agent_id)
            if row is None:
                return None
            row.pinned = pinned
        return self.get(agent_id)

    def delete(self, agent_id: str) -> dict[str, Any] | None:
        with self.session_factory.begin() as session:
            row = session.get(ManagedAgentRecord, agent_id)
            record = self._decode(row)
            if row is None:
                return None
            session.delete(row)
            return record
