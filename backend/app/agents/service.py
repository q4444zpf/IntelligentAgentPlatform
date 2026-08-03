from __future__ import annotations

import shutil
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.recorder import AuditRecorder, AuditRecordRequest
from app.core.request_context import RequestContext
from app.skills.service import SkillNotFoundError, SkillService
from app.tools.service import ToolNotFoundError, ToolService, ToolValidationError
from app.tools.store import ToolStore

from .schemas import AgentConfig, AgentCopyRequest, AgentCreateRequest, AgentInfo
from .store import (
    AgentConcurrentUpdateError,
    AgentStore,
    AgentStoreNotFoundError,
    AgentStoreProtectedError,
    AgentStoreValidationError,
)


BUILTIN_AGENT_ID = "platform-default-agent"
BUILTIN_TOOL_IDS = [
    "system.get_current_time",
    "system.get_runtime_context",
]
BUILTIN_AGENT_CONFIG = AgentConfig(
    name="水利智能体平台助手",
    description="面向水利业务的通用平台智能助手",
    runtime_form="web",
    language="zh-CN",
    system_prompt=(
        "你是水利智能体平台助手。请基于用户提供的信息提供准确、审慎的帮助；"
        "不确定时明确说明，不编造数据或执行结果。"
    ),
    context_prompt=(
        "结合当前平台页面、业务对象和会话上下文回答。涉及控制命令时，"
        "仅提供建议并等待用户确认。"
    ),
    approval_policy="control_commands",
    skill_names=[],
    tool_ids=BUILTIN_TOOL_IDS,
    enabled=True,
)


class AgentNotFoundError(Exception):
    pass


class AgentConflictError(Exception):
    pass


class AgentValidationError(Exception):
    pass


class AgentProtectedError(Exception):
    pass


class AgentService:
    def __init__(
        self,
        store: AgentStore | None = None,
        *,
        skill_service: SkillService | None = None,
        tool_service: ToolService | None = None,
        workspace_root: str | Path | None = None,
        audit_recorder: AuditRecorder | None = None,
    ):
        self.store = store or AgentStore()
        self.skill_service = skill_service or SkillService()
        self._tool_service = tool_service
        default_workspace = Path(__file__).resolve().parents[2] / "data" / "agent-workspaces"
        self.audit_recorder = audit_recorder or AuditRecorder()
        self.workspace_root = Path(workspace_root or os.getenv("AGENT_WORKSPACE_ROOT", default_workspace)).resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self._ensure_default_agent()

    @property
    def tool_service(self) -> ToolService:
        if self._tool_service is None:
            self._tool_service = ToolService(ToolStore(self.store.session_factory))
        return self._tool_service

    @staticmethod
    def _call_store_mutation(operation):
        try:
            return operation()
        except AgentStoreNotFoundError as error:
            raise AgentNotFoundError(error.agent_id) from error
        except AgentStoreValidationError as error:
            raise AgentValidationError(str(error)) from error
        except AgentStoreProtectedError as error:
            raise AgentProtectedError(str(error)) from error

    def _commit_management(self, context: RequestContext, session: Session, request_id: str | None, *, action: str, agent_id: str, name: str, risk_level: str = "medium", metadata: dict | None = None) -> None:
        metadata = metadata or {}
        try:
            self.audit_recorder.record(session, AuditRecordRequest(
                unit_id=context.unit_id, project_id=context.project_id,
                user_id=context.user_id, actor_role=context.role,
                category="management", source="agent", action=action,
                status="succeeded", risk_level=risk_level,
                resource_type="agent", resource_id=agent_id, resource_name=name,
                summary=f"Agent {agent_id} management operation succeeded",
                metadata=metadata, allowed_metadata_keys=frozenset(metadata),
                idempotency_key=f"management:{request_id or str(uuid4())}:{action}:{agent_id}",
                occurred_at=datetime.now(UTC),
            ))
            session.commit()
        except Exception:
            session.rollback()
            raise

    def _info(
        self,
        record: dict,
        default_id: str | None = None,
    ) -> AgentInfo:
        if default_id is None:
            default_id = self.store.get_default_id().agent_id
        return AgentInfo(
            **record,
            is_builtin=record["id"] == BUILTIN_AGENT_ID,
            is_default=record["id"] == default_id,
            startup_status="ready" if record["enabled"] else "disabled",
        )

    @staticmethod
    def _workspace_content(config: AgentConfig) -> str:
        return f"# {config.name}\n\n{config.system_prompt or config.description}\n"


    def _ensure_builtin_record(self) -> dict:
        workspace = self.workspace_root / BUILTIN_AGENT_ID
        workspace.mkdir(parents=True, exist_ok=True)
        agents_file = workspace / "AGENTS.md"
        if not agents_file.is_file():
            agents_file.write_text(
                self._workspace_content(BUILTIN_AGENT_CONFIG),
                encoding="utf-8",
            )

        record = self.store.get(BUILTIN_AGENT_ID)
        if record is None:
            try:
                record = self.store.create(
                    BUILTIN_AGENT_ID,
                    BUILTIN_AGENT_CONFIG.model_dump(),
                    str(workspace),
                )
            except IntegrityError:
                record = self.store.get(BUILTIN_AGENT_ID)
            if record is None:
                raise AgentConcurrentUpdateError(
                    "Built-in agent changed concurrently; retry the request"
                )
        try:
            return self.store.repair_builtin_agent(
                BUILTIN_AGENT_ID,
                BUILTIN_TOOL_IDS,
            )
        except AgentStoreNotFoundError as error:
            raise AgentConcurrentUpdateError(
                "Built-in agent changed concurrently; retry the request"
            ) from error

    def _ensure_default_agent(self) -> None:
        builtin = self._ensure_builtin_record()
        for _ in range(3):
            pointer = self.store.get_default_id()
            selected = self.store.get(pointer.agent_id) if pointer.agent_id else None
            if selected is not None and selected["enabled"]:
                return
            try:
                self.store.set_default_id(
                    builtin["id"],
                    expected_version=pointer.version,
                )
                return
            except AgentConcurrentUpdateError:
                continue
        pointer = self.store.get_default_id()
        selected = self.store.get(pointer.agent_id) if pointer.agent_id else None
        if selected is None or not selected["enabled"]:
            raise AgentConcurrentUpdateError(
                "Default agent changed concurrently; retry the request"
            )

    def _validate_skills(self, names: list[str]) -> None:
        missing = []
        for name in names:
            try:
                self.skill_service.get(name)
            except SkillNotFoundError:
                missing.append(name)
        if missing:
            raise AgentValidationError(f"Unknown skills: {', '.join(missing)}")

    def _validate_tools(self, tool_ids: list[str]) -> None:
        try:
            self.tool_service.resolve_bindable(tool_ids)
        except ToolNotFoundError as error:
            raise AgentValidationError(f"Unknown tool: {error}") from error
        except ToolValidationError as error:
            raise AgentValidationError(str(error)) from error

    def list(self) -> list[AgentInfo]:
        self._ensure_default_agent()
        default_id = self.store.get_default_id().agent_id
        return [
            self._info(record, default_id=default_id)
            for record in self.store.list()
        ]

    def get(self, agent_id: str) -> AgentInfo:
        self._ensure_default_agent()
        record = self.store.get(agent_id)
        if not record:
            raise AgentNotFoundError(agent_id)
        return self._info(record)

    def get_default(self) -> AgentInfo:
        self._ensure_default_agent()
        pointer = self.store.get_default_id()
        record = self.store.get(pointer.agent_id) if pointer.agent_id else None
        if record is None:
            raise AgentNotFoundError(pointer.agent_id or BUILTIN_AGENT_ID)
        return self._info(record)

    def set_default(self, agent_id: str, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> AgentInfo:
        self._ensure_default_agent()
        pointer = self.store.get_default_id()
        if context is None or session is None:
            record = self._call_store_mutation(lambda: self.store.set_default_agent(agent_id, expected_version=pointer.version))
        else:
            try:
                record = self._call_store_mutation(lambda: self.store.set_default_agent_in_session(session, agent_id, pointer.version))
                self._commit_management(context, session, request_id, action="resource.updated", agent_id=agent_id, name=record["name"], risk_level="high", metadata={"is_default": True})
            except Exception:
                session.rollback()
                raise
        return self._info(record, default_id=agent_id)

    def _initialize_workspace(self, agent_id: str, config: AgentConfig) -> Path:
        workspace = self.workspace_root / agent_id
        workspace.mkdir()
        (workspace / "AGENTS.md").write_text(
            f"# {config.name}\n\n{config.system_prompt or config.description}\n",
            encoding="utf-8",
        )
        return workspace

    def create(self, request: AgentCreateRequest, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> AgentInfo:
        self._ensure_default_agent()
        if self.store.get(request.id):
            raise AgentConflictError(f"Agent '{request.id}' already exists")
        self._validate_skills(request.skill_names)
        self._validate_tools(request.tool_ids)
        config = AgentConfig(**request.model_dump(exclude={"id"}))
        workspace = self._initialize_workspace(request.id, config)
        if context is None or session is None:
            try:
                record = self.store.create(request.id, config.model_dump(), str(workspace))
            except IntegrityError as error:
                shutil.rmtree(workspace, ignore_errors=True)
                raise AgentConflictError(f"Agent '{request.id}' already exists") from error
            return self._info(record)
        try:
            record = self.store.create_in_session(session, request.id, config.model_dump(), str(workspace))
            self.audit_recorder.record(session, AuditRecordRequest(
                unit_id=context.unit_id, project_id=context.project_id, user_id=context.user_id,
                actor_role=context.role, category="management", source="agent",
                action="resource.created", status="succeeded", risk_level="medium",
                resource_type="agent", resource_id=request.id, resource_name=config.name,
                summary=f"Agent {request.id} was created", metadata={"runtime_form": config.runtime_form, "enabled": config.enabled},
                allowed_metadata_keys=frozenset({"runtime_form", "enabled"}),
                idempotency_key=f"management:{request_id or str(uuid4())}:agent.create:{request.id}", occurred_at=datetime.now(UTC),
            ))
            session.commit()
        except IntegrityError as error:
            session.rollback()
            shutil.rmtree(workspace, ignore_errors=True)
            raise AgentConflictError(f"Agent '{request.id}' already exists") from error
        except Exception:
            session.rollback()
            shutil.rmtree(workspace, ignore_errors=True)
            raise
        return self._info(record)

    def update(self, agent_id: str, request: AgentConfig, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> AgentInfo:
        self._ensure_default_agent()
        self._validate_skills(request.skill_names)
        self._validate_tools(request.tool_ids)
        if context is None or session is None:
            record = self._call_store_mutation(lambda: self.store.update_agent(agent_id, request.model_dump()))
        else:
            record = self._call_store_mutation(lambda: self.store.update_agent_in_session(session, agent_id, request.model_dump()))
        workspace = Path(record["workspace_dir"])
        agents_file = workspace / "AGENTS.md"
        previous = agents_file.read_bytes() if agents_file.is_file() else None
        try:
            if workspace.is_dir():
                agents_file.write_text(
                    f"# {request.name}\n\n{request.system_prompt or request.description}\n",
                    encoding="utf-8",
                )
            if context is not None and session is not None:
                self._commit_management(context, session, request_id, action="resource.updated", agent_id=agent_id, name=request.name, metadata={"runtime_form": request.runtime_form, "enabled": request.enabled})
        except Exception:
            if session is not None:
                session.rollback()
            if previous is None:
                agents_file.unlink(missing_ok=True)
            else:
                agents_file.write_bytes(previous)
            raise
        return self._info(record)

    def set_enabled(self, agent_id: str, enabled: bool, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> AgentInfo:
        self._ensure_default_agent()
        if context is None or session is None:
            record = self._call_store_mutation(lambda: self.store.set_enabled_agent(agent_id, enabled))
        else:
            record = self._call_store_mutation(lambda: self.store.set_enabled_agent_in_session(session, agent_id, enabled))
            self._commit_management(context, session, request_id, action="resource.enabled" if enabled else "resource.disabled", agent_id=agent_id, name=record["name"], metadata={"enabled": enabled})
        return self._info(record)

    def set_pinned(self, agent_id: str, pinned: bool, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> AgentInfo:
        self._ensure_default_agent()
        record = self.store.set_pinned(agent_id, pinned) if context is None or session is None else self.store.set_pinned_in_session(session, agent_id, pinned)
        if not record:
            raise AgentNotFoundError(agent_id)
        if context is not None and session is not None:
            self._commit_management(context, session, request_id, action="resource.updated", agent_id=agent_id, name=record["name"], metadata={"pinned": pinned})
        return self._info(record)

    def copy(self, source_id: str, request: AgentCopyRequest, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> AgentInfo:
        self._ensure_default_agent()
        source = self.store.get(source_id)
        if not source:
            raise AgentNotFoundError(source_id)
        config = {name: source[name] for name in AgentConfig.model_fields}
        config["name"] = request.name
        config["skill_names"] = config["skill_names"] if request.copy_skills else []
        config["enabled"] = False
        return self.create(AgentCreateRequest(id=request.id, **config), context=context, session=session, request_id=request_id)

    def delete(self, agent_id: str, *, context: RequestContext | None = None, session: Session | None = None, request_id: str | None = None) -> None:
        self._ensure_default_agent()
        if context is None or session is None:
            record = self._call_store_mutation(lambda: self.store.delete_agent(agent_id, builtin_agent_id=BUILTIN_AGENT_ID))
            workspace = Path(record["workspace_dir"]).resolve()
            if workspace.parent == self.workspace_root and workspace.is_dir():
                shutil.rmtree(workspace)
            return
        existing = self.store.get(agent_id)
        if existing is None:
            raise AgentNotFoundError(agent_id)
        workspace = Path(existing["workspace_dir"]).resolve()
        quarantine = None
        if workspace.parent == self.workspace_root and workspace.is_dir():
            quarantine = workspace.with_name(f".{workspace.name}.quarantine-{uuid4().hex}")
            workspace.rename(quarantine)
        try:
            record = self._call_store_mutation(lambda: self.store.delete_agent_in_session(session, agent_id, builtin_agent_id=BUILTIN_AGENT_ID))
            self.audit_recorder.record(session, AuditRecordRequest(
                unit_id=context.unit_id, project_id=context.project_id, user_id=context.user_id,
                actor_role=context.role, category="management", source="agent", action="resource.deleted",
                status="succeeded", risk_level="high", resource_type="agent", resource_id=agent_id,
                resource_name=record["name"], summary=f"Agent {agent_id} was deleted",
                idempotency_key=f"management:{request_id or str(uuid4())}:agent.delete:{agent_id}", occurred_at=datetime.now(UTC),
            ))
            session.commit()
        except Exception:
            session.rollback()
            if quarantine is not None and quarantine.is_dir() and not workspace.exists():
                quarantine.rename(workspace)
            raise
        if quarantine is not None and quarantine.is_dir():
            shutil.rmtree(quarantine)
