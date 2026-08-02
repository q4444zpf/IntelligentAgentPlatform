from __future__ import annotations

import shutil
import os
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from app.skills.service import SkillNotFoundError, SkillService

from .schemas import AgentConfig, AgentCopyRequest, AgentCreateRequest, AgentInfo
from .store import AgentConcurrentUpdateError, AgentStore


BUILTIN_AGENT_ID = "platform-default-agent"
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
        workspace_root: str | Path | None = None,
    ):
        self.store = store or AgentStore()
        self.skill_service = skill_service or SkillService()
        default_workspace = Path(__file__).resolve().parents[2] / "data" / "agent-workspaces"
        self.workspace_root = Path(workspace_root or os.getenv("AGENT_WORKSPACE_ROOT", default_workspace)).resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self._ensure_default_agent()

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
        elif not record["enabled"]:
            config = {name: record[name] for name in AgentConfig.model_fields}
            config["enabled"] = True
            record = self.store.update(BUILTIN_AGENT_ID, config)
        return record

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

    def set_default(self, agent_id: str) -> AgentInfo:
        self._ensure_default_agent()
        record = self.store.get(agent_id)
        if record is None:
            raise AgentNotFoundError(agent_id)
        if not record["enabled"]:
            raise AgentValidationError(
                f"Disabled agent '{agent_id}' cannot be the default"
            )
        pointer = self.store.get_default_id()
        self.store.set_default_id(
            agent_id,
            expected_version=pointer.version,
        )
        return self._info(record, default_id=agent_id)

    def _initialize_workspace(self, agent_id: str, config: AgentConfig) -> Path:
        workspace = self.workspace_root / agent_id
        workspace.mkdir()
        (workspace / "AGENTS.md").write_text(
            f"# {config.name}\n\n{config.system_prompt or config.description}\n",
            encoding="utf-8",
        )
        return workspace

    def create(self, request: AgentCreateRequest) -> AgentInfo:
        self._ensure_default_agent()
        if self.store.get(request.id):
            raise AgentConflictError(f"Agent '{request.id}' already exists")
        self._validate_skills(request.skill_names)
        config = AgentConfig(**request.model_dump(exclude={"id"}))
        workspace = self._initialize_workspace(request.id, config)
        try:
            record = self.store.create(request.id, config.model_dump(), str(workspace))
        except IntegrityError as error:
            shutil.rmtree(workspace, ignore_errors=True)
            raise AgentConflictError(f"Agent '{request.id}' already exists") from error
        return self._info(record)

    def update(self, agent_id: str, request: AgentConfig) -> AgentInfo:
        self._ensure_default_agent()
        current = self.store.get(agent_id)
        if not current:
            raise AgentNotFoundError(agent_id)
        self._validate_skills(request.skill_names)
        if not request.enabled and self.store.get_default_id().agent_id == agent_id:
            raise AgentProtectedError(
                f"Default agent '{agent_id}' cannot be disabled"
            )
        record = self.store.update(agent_id, request.model_dump())
        workspace = Path(current["workspace_dir"])
        if workspace.is_dir():
            (workspace / "AGENTS.md").write_text(
                f"# {request.name}\n\n{request.system_prompt or request.description}\n",
                encoding="utf-8",
            )
        return self._info(record)

    def set_enabled(self, agent_id: str, enabled: bool) -> AgentInfo:
        self._ensure_default_agent()
        current = self.store.get(agent_id)
        if not current:
            raise AgentNotFoundError(agent_id)
        if not enabled and self.store.get_default_id().agent_id == agent_id:
            raise AgentProtectedError(
                f"Default agent '{agent_id}' cannot be disabled"
            )
        config = {name: current[name] for name in AgentConfig.model_fields}
        config["enabled"] = enabled
        return self._info(self.store.update(agent_id, config))

    def set_pinned(self, agent_id: str, pinned: bool) -> AgentInfo:
        self._ensure_default_agent()
        record = self.store.set_pinned(agent_id, pinned)
        if not record:
            raise AgentNotFoundError(agent_id)
        return self._info(record)

    def copy(self, source_id: str, request: AgentCopyRequest) -> AgentInfo:
        self._ensure_default_agent()
        source = self.store.get(source_id)
        if not source:
            raise AgentNotFoundError(source_id)
        config = {name: source[name] for name in AgentConfig.model_fields}
        config["name"] = request.name
        config["skill_names"] = config["skill_names"] if request.copy_skills else []
        config["enabled"] = False
        return self.create(AgentCreateRequest(id=request.id, **config))

    def delete(self, agent_id: str) -> None:
        self._ensure_default_agent()
        if agent_id == BUILTIN_AGENT_ID:
            raise AgentProtectedError(
                f"Built-in agent '{agent_id}' cannot be deleted"
            )
        if self.store.get_default_id().agent_id == agent_id:
            raise AgentProtectedError(
                f"Default agent '{agent_id}' cannot be deleted"
            )
        record = self.store.delete(agent_id)
        if not record:
            raise AgentNotFoundError(agent_id)
        workspace = Path(record["workspace_dir"]).resolve()
        if workspace.parent == self.workspace_root and workspace.is_dir():
            shutil.rmtree(workspace)
