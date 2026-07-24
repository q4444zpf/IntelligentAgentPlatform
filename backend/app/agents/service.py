from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from app.skills.service import SkillNotFoundError, SkillService

from .schemas import AgentConfig, AgentCopyRequest, AgentCreateRequest, AgentInfo
from .store import AgentStore


class AgentNotFoundError(Exception):
    pass


class AgentConflictError(Exception):
    pass


class AgentValidationError(Exception):
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
        self.workspace_root = Path(workspace_root or self.store.path.parent / "agent-workspaces").resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _info(record: dict) -> AgentInfo:
        return AgentInfo(
            **record,
            startup_status="ready" if record["enabled"] else "disabled",
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
        return [self._info(record) for record in self.store.list()]

    def get(self, agent_id: str) -> AgentInfo:
        record = self.store.get(agent_id)
        if not record:
            raise AgentNotFoundError(agent_id)
        return self._info(record)

    def _initialize_workspace(self, agent_id: str, config: AgentConfig) -> Path:
        workspace = self.workspace_root / agent_id
        workspace.mkdir()
        (workspace / "AGENTS.md").write_text(
            f"# {config.name}\n\n{config.system_prompt or config.description}\n",
            encoding="utf-8",
        )
        return workspace

    def create(self, request: AgentCreateRequest) -> AgentInfo:
        if self.store.get(request.id):
            raise AgentConflictError(f"Agent '{request.id}' already exists")
        self._validate_skills(request.skill_names)
        config = AgentConfig(**request.model_dump(exclude={"id"}))
        workspace = self._initialize_workspace(request.id, config)
        try:
            record = self.store.create(request.id, config.model_dump(), str(workspace))
        except sqlite3.IntegrityError as error:
            shutil.rmtree(workspace, ignore_errors=True)
            raise AgentConflictError(f"Agent '{request.id}' already exists") from error
        return self._info(record)

    def update(self, agent_id: str, request: AgentConfig) -> AgentInfo:
        current = self.store.get(agent_id)
        if not current:
            raise AgentNotFoundError(agent_id)
        self._validate_skills(request.skill_names)
        record = self.store.update(agent_id, request.model_dump())
        workspace = Path(current["workspace_dir"])
        if workspace.is_dir():
            (workspace / "AGENTS.md").write_text(
                f"# {request.name}\n\n{request.system_prompt or request.description}\n",
                encoding="utf-8",
            )
        return self._info(record)

    def set_enabled(self, agent_id: str, enabled: bool) -> AgentInfo:
        current = self.store.get(agent_id)
        if not current:
            raise AgentNotFoundError(agent_id)
        config = {name: current[name] for name in AgentConfig.model_fields}
        config["enabled"] = enabled
        return self._info(self.store.update(agent_id, config))

    def set_pinned(self, agent_id: str, pinned: bool) -> AgentInfo:
        record = self.store.set_pinned(agent_id, pinned)
        if not record:
            raise AgentNotFoundError(agent_id)
        return self._info(record)

    def copy(self, source_id: str, request: AgentCopyRequest) -> AgentInfo:
        source = self.store.get(source_id)
        if not source:
            raise AgentNotFoundError(source_id)
        config = {name: source[name] for name in AgentConfig.model_fields}
        config["name"] = request.name
        config["skill_names"] = config["skill_names"] if request.copy_skills else []
        config["enabled"] = False
        return self.create(AgentCreateRequest(id=request.id, **config))

    def delete(self, agent_id: str) -> None:
        record = self.store.delete(agent_id)
        if not record:
            raise AgentNotFoundError(agent_id)
        workspace = Path(record["workspace_dir"]).resolve()
        if workspace.parent == self.workspace_root and workspace.is_dir():
            shutil.rmtree(workspace)
