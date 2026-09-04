from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import SessionFactory
from app.db.platform_models import ManagedAgentRecord, PlatformSettingRecord


DEFAULT_SETTING_KEY = "default_agent"
INTERNAL_SCOPE_ID = "__internal__"


class AgentConcurrentUpdateError(ValueError):
    pass


class AgentStoreNotFoundError(ValueError):
    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        super().__init__(f"Agent '{agent_id}' was not found")


class AgentStoreValidationError(ValueError):
    pass


class AgentStoreProtectedError(ValueError):
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
            "availability_scope": row.availability_scope,
            "unit_id": row.unit_id,
            "project_id": row.project_id,
            "allowed_project_ids": list(row.allowed_project_ids),
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

    def get_for_update(
        self,
        session: Session,
        agent_id: str,
    ) -> dict[str, Any] | None:
        return self._decode(self._lock_agent(session, agent_id))

    @staticmethod
    def _decode_default_agent_id(
        row: PlatformSettingRecord | None,
    ) -> str | None:
        if row is None or not isinstance(row.value, dict):
            return None
        agent_id = row.value.get("agent_id")
        if row.value.get("scope") != "platform" or not isinstance(agent_id, str):
            return None
        return agent_id

    @staticmethod
    def _lock_default_pointer(
        session: Session,
    ) -> PlatformSettingRecord | None:
        return session.scalar(
            select(PlatformSettingRecord)
            .where(PlatformSettingRecord.setting_key == DEFAULT_SETTING_KEY)
            .with_for_update()
        )

    @staticmethod
    def _lock_agent(
        session: Session,
        agent_id: str,
    ) -> ManagedAgentRecord | None:
        return session.scalar(
            select(ManagedAgentRecord)
            .where(ManagedAgentRecord.agent_id == agent_id)
            .with_for_update()
        )

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

    @staticmethod
    def is_platform_default_eligible(record: dict[str, Any] | None) -> bool:
        return bool(
            record
            and record.get("enabled") is True
            and record.get("availability_scope") == "common"
            and record.get("unit_id") is None
            and record.get("project_id") is None
            and record.get("allowed_project_ids") == ["*"]
        )

    def _validate_platform_default_target(
        self,
        target: ManagedAgentRecord,
        agent_id: str,
    ) -> None:
        if not target.config.get("enabled", False):
            raise AgentStoreValidationError(
                f"Disabled agent '{agent_id}' cannot be the default"
            )
        if not self.is_platform_default_eligible(self._decode(target)):
            raise AgentStoreValidationError(
                f"Default agent '{agent_id}' must be available to every project"
            )

    def set_default_id(
        self,
        agent_id: str,
        expected_version: int,
    ) -> DefaultAgentPointer:
        self.set_default_agent(agent_id, expected_version)
        return DefaultAgentPointer(
            agent_id=agent_id,
            version=expected_version + 1,
        )

    def set_default_agent(
        self,
        agent_id: str,
        expected_version: int,
    ) -> dict[str, Any]:
        try:
            with self.session_factory.begin() as session:
                pointer = self._lock_default_pointer(session)
                current_version = pointer.version if pointer is not None else 0
                if current_version != expected_version:
                    raise AgentConcurrentUpdateError(
                        "Default agent changed concurrently; retry the request"
                    )

                target = self._lock_agent(session, agent_id)
                if target is None:
                    raise AgentStoreNotFoundError(agent_id)
                self._validate_platform_default_target(target, agent_id)

                value = {"agent_id": agent_id, "scope": "platform"}
                if pointer is None:
                    session.add(
                        PlatformSettingRecord(
                            setting_key=DEFAULT_SETTING_KEY,
                            value=value,
                        )
                    )
                else:
                    pointer.value = value
                    pointer.version = current_version + 1

                session.flush()
                session.refresh(target)
                return self._decode(target)
        except IntegrityError as error:
            raise AgentConcurrentUpdateError(
                "Default agent changed concurrently; retry the request"
            ) from error

    def update_agent(
        self,
        agent_id: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            pointer = self._lock_default_pointer(session)
            default_id = self._decode_default_agent_id(pointer)
            target = self._lock_agent(session, agent_id)
            if target is None:
                raise AgentStoreNotFoundError(agent_id)
            if not config.get("enabled", False) and default_id == agent_id:
                raise AgentStoreProtectedError(
                    f"Default agent '{agent_id}' cannot be disabled"
                )

            target.config = config
            session.flush()
            session.refresh(target)
            return self._decode(target)

    def repair_builtin_agent(
        self,
        agent_id: str,
        required_tool_ids: list[str],
    ) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            target = self._lock_agent(session, agent_id)
            if target is None:
                raise AgentStoreNotFoundError(agent_id)

            config = dict(target.config)
            existing_tool_ids = config.get("tool_ids", [])
            if not isinstance(existing_tool_ids, list):
                existing_tool_ids = []
            repaired_tool_ids = list(
                dict.fromkeys([*existing_tool_ids, *required_tool_ids])
            )
            if (
                not config.get("enabled", False)
                or repaired_tool_ids != existing_tool_ids
            ):
                config["enabled"] = True
                config["tool_ids"] = repaired_tool_ids
                target.config = config
                session.flush()

            session.refresh(target)
            return self._decode(target)

    def set_enabled_agent(
        self,
        agent_id: str,
        enabled: bool,
    ) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            pointer = self._lock_default_pointer(session)
            default_id = self._decode_default_agent_id(pointer)
            target = self._lock_agent(session, agent_id)
            if target is None:
                raise AgentStoreNotFoundError(agent_id)
            if not enabled and default_id == agent_id:
                raise AgentStoreProtectedError(
                    f"Default agent '{agent_id}' cannot be disabled"
                )

            config = dict(target.config)
            config["enabled"] = enabled
            target.config = config
            session.flush()
            session.refresh(target)
            return self._decode(target)

    def set_default_agent_in_session(self, session: Session, agent_id: str, expected_version: int) -> dict[str, Any]:
        pointer = self._lock_default_pointer(session)
        current_version = pointer.version if pointer is not None else 0
        if current_version != expected_version:
            raise AgentConcurrentUpdateError("Default agent changed concurrently; retry the request")
        target = self._lock_agent(session, agent_id)
        if target is None:
            raise AgentStoreNotFoundError(agent_id)
        self._validate_platform_default_target(target, agent_id)
        value = {"agent_id": agent_id, "scope": "platform"}
        if pointer is None:
            session.add(PlatformSettingRecord(setting_key=DEFAULT_SETTING_KEY, value=value))
        else:
            pointer.value = value
            pointer.version = current_version + 1
        session.flush()
        session.refresh(target)
        return self._decode(target)

    def update_agent_in_session(self, session: Session, agent_id: str, config: dict[str, Any]) -> dict[str, Any]:
        pointer = self._lock_default_pointer(session)
        default_id = self._decode_default_agent_id(pointer)
        target = self._lock_agent(session, agent_id)
        if target is None:
            raise AgentStoreNotFoundError(agent_id)
        if not config.get("enabled", False) and default_id == agent_id:
            raise AgentStoreProtectedError(f"Default agent '{agent_id}' cannot be disabled")
        target.config = config
        session.flush()
        session.refresh(target)
        return self._decode(target)

    def set_enabled_agent_in_session(self, session: Session, agent_id: str, enabled: bool) -> dict[str, Any]:
        pointer = self._lock_default_pointer(session)
        default_id = self._decode_default_agent_id(pointer)
        target = self._lock_agent(session, agent_id)
        if target is None:
            raise AgentStoreNotFoundError(agent_id)
        if not enabled and default_id == agent_id:
            raise AgentStoreProtectedError(f"Default agent '{agent_id}' cannot be disabled")
        config = dict(target.config)
        config["enabled"] = enabled
        target.config = config
        session.flush()
        session.refresh(target)
        return self._decode(target)

    def set_pinned_in_session(self, session: Session, agent_id: str, pinned: bool) -> dict[str, Any] | None:
        row = session.get(ManagedAgentRecord, agent_id)
        if row is None:
            return None
        row.pinned = pinned
        session.flush()
        session.refresh(row)
        return self._decode(row)

    def delete_agent(
        self,
        agent_id: str,
        *,
        builtin_agent_id: str,
    ) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            pointer = self._lock_default_pointer(session)
            default_id = self._decode_default_agent_id(pointer)
            target = self._lock_agent(session, agent_id)
            if target is None:
                raise AgentStoreNotFoundError(agent_id)
            if agent_id == builtin_agent_id:
                raise AgentStoreProtectedError(
                    f"Built-in agent '{agent_id}' cannot be deleted"
                )
            if default_id == agent_id:
                raise AgentStoreProtectedError(
                    f"Default agent '{agent_id}' cannot be deleted"
                )

            record = self._decode(target)
            session.delete(target)
            session.flush()
            return record

    def delete_agent_in_session(self, session: Session, agent_id: str, *, builtin_agent_id: str) -> dict[str, Any]:
        pointer = self._lock_default_pointer(session)
        default_id = self._decode_default_agent_id(pointer)
        target = self._lock_agent(session, agent_id)
        if target is None:
            raise AgentStoreNotFoundError(agent_id)
        if agent_id == builtin_agent_id:
            raise AgentStoreProtectedError(f"Built-in agent '{agent_id}' cannot be deleted")
        if default_id == agent_id:
            raise AgentStoreProtectedError(f"Default agent '{agent_id}' cannot be deleted")
        record = self._decode(target)
        session.delete(target)
        session.flush()
        return record

    def create_in_session(
        self,
        session: Session,
        agent_id: str,
        config: dict[str, Any],
        workspace_dir: str,
        *,
        availability_scope: str = "project",
        unit_id: str | None = INTERNAL_SCOPE_ID,
        project_id: str | None = INTERNAL_SCOPE_ID,
        allowed_project_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        allowed = list(allowed_project_ids or [])
        if availability_scope == "project":
            if not unit_id or not project_id or allowed:
                raise AgentStoreValidationError("Project Agent availability is invalid")
        elif availability_scope == "common":
            if unit_id is not None or project_id is not None or not allowed:
                raise AgentStoreValidationError("Common Agent availability is invalid")
            if "*" in allowed and allowed != ["*"]:
                raise AgentStoreValidationError("Common wildcard must be the only allowlist value")
        else:
            raise AgentStoreValidationError("Agent availability scope is invalid")
        row = ManagedAgentRecord(
            agent_id=agent_id,
            config=config,
            workspace_dir=workspace_dir,
            pinned=False,
            availability_scope=availability_scope,
            unit_id=unit_id,
            project_id=project_id,
            allowed_project_ids=allowed,
        )
        session.add(row)
        session.flush()
        session.refresh(row)
        return self._decode(row)

    def create(
        self,
        agent_id: str,
        config: dict[str, Any],
        workspace_dir: str,
        **availability: Any,
    ) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            return self.create_in_session(
                session, agent_id, config, workspace_dir, **availability
            )

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
