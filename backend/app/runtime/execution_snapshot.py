from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import JSON, DateTime, Index, String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.base import Base


class SnapshotIntegrityError(ValueError):
    pass


class PublishedAgentSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str
    description: str
    runtime_form: str
    language: str
    system_prompt: str
    context_prompt: str
    approval_policy: str
    kind: Literal["agent"] = "agent"


class SnapshotModelSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str
    model: str


class SnapshotMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    sequence: int
    role: str
    content: str
    created_at: datetime


MAX_SKILL_RESOURCE_FILES = 500
MAX_SKILL_RESOURCE_SINGLE_FILE_BYTES = 10 * 1024 * 1024
MAX_SKILL_RESOURCE_TOTAL_BYTES = 20 * 1024 * 1024
# Short aliases for callers that prefer the budget terminology.
MAX_SKILL_FILE_COUNT = MAX_SKILL_RESOURCE_FILES
MAX_SKILL_FILE_BYTES = MAX_SKILL_RESOURCE_SINGLE_FILE_BYTES
MAX_SKILL_TOTAL_BYTES = MAX_SKILL_RESOURCE_TOTAL_BYTES
SKILL_RESOURCE_MAX_FILES = MAX_SKILL_RESOURCE_FILES
SKILL_RESOURCE_MAX_FILE_BYTES = MAX_SKILL_RESOURCE_SINGLE_FILE_BYTES
SKILL_RESOURCE_MAX_TOTAL_BYTES = MAX_SKILL_RESOURCE_TOTAL_BYTES


class SnapshotSkillFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    size: int = Field(ge=0)
    sha256: str
    content_base64: str | None = None

    @field_validator("path")
    @classmethod
    def _canonical_path(cls, value: str) -> str:
        if not value or "\\" in value:
            raise ValueError("Skill resource path must be a canonical relative POSIX path")
        if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
            raise ValueError("Skill resource path must be relative")
        path = PurePosixPath(value)
        if path == PurePosixPath(".") or ".." in path.parts or path.as_posix() != value:
            raise ValueError("Skill resource path must be a canonical relative POSIX path")
        return value

    @field_validator("sha256")
    @classmethod
    def _sha256(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("Skill resource sha256 must be lowercase SHA-256")
        return value

    @model_validator(mode="after")
    def _verify_embedded_content(self):
        if self.content_base64 is None:
            return self
        try:
            content = base64.b64decode(self.content_base64, validate=True)
        except Exception as error:
            raise ValueError("Skill resource content_base64 is invalid") from error
        if len(content) != self.size:
            raise ValueError("Skill resource embedded content size mismatch")
        if hashlib.sha256(content).hexdigest() != self.sha256:
            raise ValueError("Skill resource embedded content digest mismatch")
        return self


class SnapshotSkill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str = ""
    version: str = ""
    content: str = ""
    source: str = ""
    enabled: bool = True
    tags: tuple[str, ...] = ()
    metadata: dict[str, object] = Field(default_factory=dict)
    file_count: int = 1
    updated_at: datetime | None = None
    skill_id: str | None = None
    version_id: str | None = None
    package_digest: str | None = None
    object_key: str | None = None
    archive_sha256: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    files: tuple[SnapshotSkillFile, ...] = ()

    @model_validator(mode="after")
    def _validate_resources(self):
        if len(self.files) > MAX_SKILL_RESOURCE_FILES:
            raise ValueError(
                f"Skill resource file count exceeds {MAX_SKILL_RESOURCE_FILES}"
            )
        paths = [item.path.casefold() for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("duplicate Skill resource paths")
        if any(item.size > MAX_SKILL_RESOURCE_SINGLE_FILE_BYTES for item in self.files):
            raise ValueError(
                f"Skill resource file exceeds {MAX_SKILL_RESOURCE_SINGLE_FILE_BYTES} bytes"
            )
        total = sum(item.size for item in self.files)
        if total > MAX_SKILL_RESOURCE_TOTAL_BYTES:
            raise ValueError(
                f"Skill resource total exceeds {MAX_SKILL_RESOURCE_TOTAL_BYTES} bytes"
            )
        return self


class SnapshotKnowledgeSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_id: str
    version: str = ""
    name: str = ""
    description: str = ""
    source: str = "knowledge"
    risk_level: str = "low"
    input_schema: dict[str, object] = Field(default_factory=dict)
    output_schema: dict[str, object] = Field(default_factory=dict)
    source_resource_id: str | None = None
    source_capability_id: str | None = None
    source_available: bool = True
    requires_approval: bool = False
    published: bool = True
    enabled: bool = True


class SnapshotTool(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_id: str
    version: str
    name: str
    description: str
    input_schema: dict[str, object]
    output_schema: dict[str, object] = Field(default_factory=dict)
    source: str = ""
    risk_level: str = "low"
    source_resource_id: str | None = None
    source_capability_id: str | None = None
    requires_approval: bool = False
    published: bool
    enabled: bool
    source_available: bool


class SnapshotTeamMember(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str
    role: Literal["supervisor", "member"]
    responsibility: str
    agent_definition_digest: str | None = None
    agent: PublishedAgentSnapshot | None = None
    model: SnapshotModelSelection | None = None
    skill_names: tuple[str, ...] = ()
    tool_ids: tuple[str, ...] = ()
    knowledge_source_ids: tuple[str, ...] = ()
    skills: tuple[SnapshotSkill, ...] = ()
    knowledge_sources: tuple[SnapshotKnowledgeSource, ...] = ()
    tools: tuple[SnapshotTool, ...] = ()


class PublishedTeamSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["team"] = "team"
    id: str
    version_id: str
    version: int
    definition_digest: str
    supervisor: SnapshotTeamMember
    members: tuple[SnapshotTeamMember, ...]
    max_steps: int
    max_parallel_members: int
    timeout_seconds: int
    failure_strategy: str
    tool_ids: tuple[str, ...] = ()
    skill_names: tuple[str, ...] = ()
    knowledge_source_ids: tuple[str, ...] = ()
    approval_policy_id: str | None = None
    name: str
    description: str
    runtime_form: str
    language: str
    system_prompt: str
    context_prompt: str
    approval_policy: str


class SnapshotRuntimeLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_max_bytes: int = Field(gt=0)
    max_iterations: int = Field(default=4, gt=0)
    max_tool_calls: int = Field(default=8, ge=0)
    max_subagents: int = Field(default=4, ge=0)
    max_output_bytes: int = Field(default=4 * 1024 * 1024, gt=0)


class ExecutionSnapshotPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1", "2", "3", "4", "5"] = "1"
    snapshot_id: str
    run_id: str
    unit_id: str
    project_id: str
    user_id: str
    actor: PublishedAgentSnapshot | PublishedTeamSnapshot
    model: SnapshotModelSelection
    messages: tuple[SnapshotMessage, ...]
    skills: tuple[SnapshotSkill, ...] = ()
    knowledge_sources: tuple[SnapshotKnowledgeSource, ...] = ()
    tools: tuple[SnapshotTool, ...] = ()
    limits: SnapshotRuntimeLimits
    created_at: datetime


class StoredExecutionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str
    run_id: str
    digest: str
    payload: ExecutionSnapshotPayload
    created_at: datetime
    expires_at: datetime | None


class RuntimeExecutionSnapshot(Base):
    __tablename__ = "runtime_execution_snapshots"
    __table_args__ = (
        Index(
            "ix_runtime_execution_snapshots_run_id",
            "run_id",
            unique=True,
        ),
    )

    snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


_V4_TOOL_FIELDS = (
    "tool_id",
    "version",
    "name",
    "description",
    "input_schema",
    "published",
    "enabled",
    "source_available",
)


def _frozen_v4_projection(serialized: dict) -> dict:
    def skill(value: dict) -> dict:
        projected = {"name": value.get("name", "")}
        resource_fields = (
            "skill_id",
            "version_id",
            "package_digest",
            "object_key",
            "archive_sha256",
            "size_bytes",
            "files",
        )
        if any(value.get(field) for field in resource_fields):
            projected.update({field: value.get(field) for field in resource_fields})
        return projected

    def knowledge(value: dict) -> dict:
        return {"tool_id": value["tool_id"]}

    def tool(value: dict) -> dict:
        return {field: value[field] for field in _V4_TOOL_FIELDS}

    serialized["skills"] = [skill(item) for item in serialized.get("skills", ())]
    serialized["knowledge_sources"] = [
        knowledge(item) for item in serialized.get("knowledge_sources", ())
    ]
    serialized["tools"] = [tool(item) for item in serialized.get("tools", ())]
    actor = serialized.get("actor", {})
    if actor.get("kind") == "team":
        for field in (
            "tool_ids",
            "skill_names",
            "knowledge_source_ids",
            "approval_policy_id",
        ):
            actor.pop(field, None)
        for member in [actor.get("supervisor"), *actor.get("members", ())]:
            if not isinstance(member, dict):
                continue
            member["skills"] = [skill(item) for item in member.get("skills", ())]
            member["knowledge_sources"] = [
                knowledge(item) for item in member.get("knowledge_sources", ())
            ]
            member["tools"] = [tool(item) for item in member.get("tools", ())]
    return serialized


def canonical_snapshot_bytes(payload: ExecutionSnapshotPayload) -> bytes:
    serialized = payload.model_dump(mode="json")
    if payload.schema_version in {"1", "2", "3", "4"}:
        serialized = _frozen_v4_projection(serialized)
    if payload.schema_version in {"1", "2", "3"}:
        serialized.get("actor", {}).pop("kind", None)
    if payload.schema_version == "1":
        serialized.pop("tools", None)
    if payload.schema_version in {"1", "2"}:
        serialized["limits"] = {
            "snapshot_max_bytes": payload.limits.snapshot_max_bytes,
        }
    return json.dumps(
        serialized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def verify_snapshot_digest(payload: ExecutionSnapshotPayload, digest: str) -> bool:
    expected = hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest()
    return hmac.compare_digest(expected, digest)


def team_execution_deadline(
    snapshot: StoredExecutionSnapshot,
) -> datetime | None:
    actor = snapshot.payload.actor
    if not isinstance(actor, PublishedTeamSnapshot):
        return None
    created_at = snapshot.created_at
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        created_at = created_at.replace(tzinfo=UTC)
    return created_at.astimezone(UTC) + timedelta(seconds=actor.timeout_seconds)


class ExecutionSnapshotService:
    def __init__(
        self,
        session: Session,
        agent_service,
        conversation_repository,
        *,
        max_bytes: int | None = None,
        max_iterations: int | None = None,
        max_tool_calls: int | None = None,
        max_subagents: int | None = None,
        max_output_bytes: int | None = None,
        clock=None,
    ):
        self.session = session
        self.agent_service = agent_service
        self.conversation_repository = conversation_repository
        self.max_bytes = max_bytes or int(
            os.getenv("IAP_RUNNER_SNAPSHOT_MAX_BYTES", "1048576")
        )
        self.max_iterations = max_iterations or int(
            os.getenv("IAP_RUNNER_MAX_ITERATIONS", "4")
        )
        self.max_tool_calls = (
            max_tool_calls
            if max_tool_calls is not None
            else int(os.getenv("IAP_RUNNER_MAX_TOOL_CALLS", "8"))
        )
        self.max_subagents = (
            max_subagents
            if max_subagents is not None
            else int(os.getenv("IAP_RUNNER_MAX_SUBAGENTS", "4"))
        )
        self.max_output_bytes = max_output_bytes or int(
            os.getenv("IAP_RUNNER_MAX_OUTPUT_BYTES", str(4 * 1024 * 1024))
        )
        self.clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def _stored(row: RuntimeExecutionSnapshot) -> StoredExecutionSnapshot:
        stored = StoredExecutionSnapshot(
            snapshot_id=row.snapshot_id,
            run_id=row.run_id,
            digest=row.digest,
            payload=ExecutionSnapshotPayload.model_validate(row.payload),
            created_at=row.created_at,
            expires_at=row.expires_at,
        )
        if not verify_snapshot_digest(stored.payload, stored.digest):
            raise SnapshotIntegrityError("execution snapshot digest mismatch")
        return stored

    def get(self, snapshot_id: str) -> StoredExecutionSnapshot | None:
        row = self.session.get(RuntimeExecutionSnapshot, snapshot_id)
        return self._stored(row) if row is not None else None

    def get_for_run(self, run_id: str) -> StoredExecutionSnapshot | None:
        row = self.session.scalar(
            select(RuntimeExecutionSnapshot).where(
                RuntimeExecutionSnapshot.run_id == run_id
            ).execution_options(populate_existing=True)
        )
        return self._stored(row) if row is not None else None

    @staticmethod
    def _definition_digest(definition: dict) -> str:
        serialized = json.dumps(
            definition,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()

    def _snapshot_skill(self, name: str, skill) -> SnapshotSkill:
        fields = {
            "name": skill.name,
            "description": skill.description,
            "version": skill.version,
            "content": skill.content,
            "source": skill.source,
            "enabled": skill.enabled,
            "tags": tuple(skill.tags),
            "metadata": skill.metadata,
            "file_count": skill.file_count,
            "updated_at": skill.updated_at,
            "skill_id": getattr(skill, "skill_id", None),
            "version_id": getattr(skill, "version_id", None),
            "package_digest": getattr(skill, "package_digest", None),
            "object_key": getattr(skill, "object_key", None),
            "archive_sha256": getattr(skill, "archive_sha256", None),
            "size_bytes": getattr(skill, "size_bytes", None),
            "files": (),
        }
        skill_service = getattr(self.agent_service, "skill_service", None)
        reader = getattr(skill_service, "read_files", None)
        if callable(reader):
            try:
                resources = reader(name)
                fields["files"] = tuple(
                    SnapshotSkillFile(
                        path=path,
                        size=len(content),
                        sha256=hashlib.sha256(content).hexdigest(),
                        content_base64=(
                            None
                            if fields["object_key"]
                            else base64.b64encode(content).decode("ascii")
                        ),
                    )
                    for path, content in resources
                )
            except Exception as error:
                raise SnapshotIntegrityError(
                    f"Unable to read resources for Skill '{name}'"
                ) from error
        return SnapshotSkill(**fields)

    @classmethod
    def _team_member_snapshot(cls, member: dict) -> SnapshotTeamMember:
        definition = member.get("agent_definition")
        digest = member.get("agent_definition_digest")
        if not isinstance(definition, dict) or not digest:
            raise SnapshotIntegrityError("Published Agent definition is missing")
        if not hmac.compare_digest(cls._definition_digest(definition), digest):
            raise SnapshotIntegrityError("Agent definition digest mismatch")
        try:
            agent = PublishedAgentSnapshot(
                id=definition["id"],
                name=definition["name"],
                description=definition["description"],
                runtime_form=definition["runtime_form"],
                language=definition["language"],
                system_prompt=definition["system_prompt"],
                context_prompt=definition["context_prompt"],
                approval_policy=definition["approval_policy"],
            )
            model = SnapshotModelSelection(
                provider_id=definition["provider_id"],
                model=definition["model"],
            )
            captured_tools = {
                tool["tool_id"]: tool for tool in definition.get("tools", ())
            }
            tools = tuple(
                SnapshotTool.model_validate(captured_tools[tool_id])
                for tool_id in member.get("tool_ids", ())
            )
            captured_skills = {
                skill["name"]: skill for skill in definition.get("skills", ())
            }
            skills = tuple(
                SnapshotSkill.model_validate(captured_skills[name])
                for name in member.get("skill_names", ())
            )
            captured_knowledge = {
                source["tool_id"]: source
                for source in definition.get("knowledge_sources", ())
            }
            knowledge_sources = tuple(
                SnapshotKnowledgeSource.model_validate(captured_knowledge[source_id])
                for source_id in member.get("knowledge_source_ids", ())
            )
        except (KeyError, TypeError, ValueError) as error:
            raise SnapshotIntegrityError(
                "Published Agent definition is incomplete"
            ) from error
        return SnapshotTeamMember(
            agent_id=member["agent_id"],
            role=member["role"],
            responsibility=member["responsibility"],
            agent_definition_digest=digest,
            agent=agent,
            model=model,
            skill_names=tuple(member["skill_names"]),
            tool_ids=tuple(member["tool_ids"]),
            knowledge_source_ids=tuple(member["knowledge_source_ids"]),
            skills=skills,
            knowledge_sources=knowledge_sources,
            tools=tools,
        )

    @classmethod
    def _verify_team_version(cls, team_version) -> dict:
        digest = team_version.definition_digest
        if not digest or not hmac.compare_digest(
            cls._definition_digest(team_version.definition), digest
        ):
            raise SnapshotIntegrityError("Team definition digest mismatch")
        definition = team_version.definition
        defined_members = [
            definition.get("supervisor"),
            *definition.get("members", ()),
        ]
        if any(not isinstance(member, dict) for member in defined_members):
            raise SnapshotIntegrityError("Team member definition is missing")
        stored_members = sorted(team_version.members, key=lambda item: item.position)
        if len(stored_members) != len(defined_members):
            raise SnapshotIntegrityError("Team member count mismatch")
        for position, (stored, defined) in enumerate(
            zip(stored_members, defined_members, strict=True)
        ):
            expected = {
                "agent_id": defined.get("agent_id"),
                "role": defined.get("role"),
                "responsibility": defined.get("responsibility"),
                "position": position,
                "tool_ids": defined.get("tool_ids"),
                "skill_names": defined.get("skill_names"),
                "knowledge_source_ids": defined.get("knowledge_source_ids"),
                "agent_definition": defined.get("agent_definition"),
                "agent_definition_digest": defined.get("agent_definition_digest"),
            }
            actual = {
                "agent_id": stored.agent_id,
                "role": stored.role,
                "responsibility": stored.responsibility,
                "position": stored.position,
                "tool_ids": stored.tool_ids,
                "skill_names": stored.skill_names,
                "knowledge_source_ids": stored.knowledge_source_ids,
                "agent_definition": stored.agent_definition,
                "agent_definition_digest": stored.agent_definition_digest,
            }
            cls._team_member_snapshot(actual)
            if actual != expected:
                raise SnapshotIntegrityError("Team member definition mismatch")
            cls._team_member_snapshot(defined)
        mirrored = {
            "tool_ids": team_version.tool_ids,
            "skill_names": team_version.skill_names,
            "knowledge_source_ids": team_version.knowledge_source_ids,
            "max_steps": team_version.max_steps,
            "max_parallel_members": team_version.max_parallel_members,
            "timeout_seconds": team_version.timeout_seconds,
            "failure_strategy": team_version.failure_strategy,
            "approval_policy_id": team_version.approval_policy_id,
        }
        if mirrored != {field: definition.get(field) for field in mirrored}:
            raise SnapshotIntegrityError("Team effective state mismatch")
        return definition

    def create(self, run_id: str) -> StoredExecutionSnapshot:
        existing = self.session.scalar(
            select(RuntimeExecutionSnapshot).where(
                RuntimeExecutionSnapshot.run_id == run_id
            )
        )
        if existing is not None:
            return self._stored(existing)

        context = self.conversation_repository.get_run_execution_context(run_id)
        run = self.conversation_repository.get_run_by_id(run_id)
        if context is None or run is None:
            raise KeyError(run_id)
        if getattr(run, "actor_type", "agent") == "team":
            if not getattr(run, "actor_version_id", None):
                raise ValueError("Team Run has no selected version")
            from app.collaboration.repository import TeamRepository

            team_version = TeamRepository(self.session).get_published_version_scoped(
                str(context["unit_id"]),
                str(context["project_id"]),
                run.actor_id,
                run.actor_version_id,
            )
            if team_version is None:
                raise ValueError("Selected Team version is unavailable")
            definition = self._verify_team_version(team_version)
            supervisor_definition = definition.get("supervisor")
            if not isinstance(supervisor_definition, dict):
                raise ValueError("Published Team has no supervisor")
            supervisor_snapshot = self._team_member_snapshot(supervisor_definition)
            member_snapshots = tuple(
                self._team_member_snapshot(member)
                for member in definition.get("members", ())
            )
            if supervisor_snapshot.agent is None or supervisor_snapshot.model is None:
                raise SnapshotIntegrityError("Published supervisor definition is incomplete")
            captured_agent_definitions = [
                supervisor_definition["agent_definition"],
                *(member["agent_definition"] for member in definition["members"]),
            ]
            captured_tools = {
                tool["tool_id"]: SnapshotTool.model_validate(tool)
                for agent_definition in captured_agent_definitions
                for tool in agent_definition.get("tools", ())
            }
            captured_skills = {
                skill["name"]: SnapshotSkill.model_validate(skill)
                for agent_definition in captured_agent_definitions
                for skill in agent_definition.get("skills", ())
            }
            captured_knowledge = {
                source["tool_id"]: SnapshotKnowledgeSource.model_validate(source)
                for agent_definition in captured_agent_definitions
                for source in agent_definition.get("knowledge_sources", ())
            }
            try:
                snapshot_tools = tuple(
                    captured_tools[tool_id] for tool_id in definition["tool_ids"]
                )
                snapshot_skills = tuple(
                    captured_skills[name] for name in definition["skill_names"]
                )
                snapshot_knowledge_sources = tuple(
                    captured_knowledge[source_id]
                    for source_id in definition["knowledge_source_ids"]
                )
            except KeyError as error:
                raise SnapshotIntegrityError(
                    "Team capability whitelist is missing a captured definition"
                ) from error
            knowledge_tools = tuple(
                SnapshotTool.model_validate(source.model_dump(mode="json"))
                for source in snapshot_knowledge_sources
            )
            snapshot_tools = tuple(
                {
                    tool.tool_id: tool
                    for tool in (*snapshot_tools, *knowledge_tools)
                }.values()
            )
            actor = PublishedTeamSnapshot(
                id=team_version.team_id,
                version_id=team_version.id,
                version=team_version.version,
                definition_digest=team_version.definition_digest,
                supervisor=supervisor_snapshot,
                members=member_snapshots,
                max_steps=definition["max_steps"],
                max_parallel_members=definition["max_parallel_members"],
                timeout_seconds=definition["timeout_seconds"],
                failure_strategy=definition["failure_strategy"],
                tool_ids=tuple(definition["tool_ids"]),
                skill_names=tuple(definition["skill_names"]),
                knowledge_source_ids=tuple(definition["knowledge_source_ids"]),
                approval_policy_id=definition["approval_policy_id"],
                name=definition.get("name", team_version.team.name),
                description=definition.get(
                    "description", team_version.team.description
                ),
                runtime_form=supervisor_snapshot.agent.runtime_form,
                language=supervisor_snapshot.agent.language,
                system_prompt=supervisor_snapshot.agent.system_prompt,
                context_prompt=supervisor_snapshot.agent.context_prompt,
                approval_policy=supervisor_snapshot.agent.approval_policy,
            )
            snapshot_model = supervisor_snapshot.model
            schema_version = "5"
        else:
            agent = self.agent_service.get(run.actor_id)
            actor = PublishedAgentSnapshot(
                id=agent.id,
                name=agent.name,
                description=agent.description,
                runtime_form=agent.runtime_form,
                language=agent.language,
                system_prompt=agent.system_prompt,
                context_prompt=agent.context_prompt,
                approval_policy=agent.approval_policy,
            )
            schema_version = "3"
            if not agent.enabled:
                raise ValueError(f"Agent '{agent.id}' is disabled")
            tools = self.agent_service.tool_service.resolve_bindable(agent.tool_ids)
            knowledge_sources = (
                self.agent_service.tool_service.resolve_knowledge_sources(
                    agent.knowledge_source_ids
                )
            )
            snapshot_model = SnapshotModelSelection(
                provider_id=agent.provider_id,
                model=agent.model,
            )
            snapshot_skills = tuple(
                self._snapshot_skill(name, skill)
                for name in agent.skill_names
                for skill in (self.agent_service.skill_service.get(name),)
                if skill.enabled
            )
            snapshot_knowledge_sources = tuple(
                SnapshotKnowledgeSource(
                    tool_id=source.tool_id,
                    version=source.version,
                    name=source.name,
                    description=source.description,
                    source=source.source,
                    risk_level=source.risk_level,
                    input_schema=source.input_schema,
                    output_schema=source.output_schema,
                    source_resource_id=source.source_resource_id,
                    source_capability_id=source.source_capability_id,
                    source_available=source.source_available,
                    requires_approval=source.requires_approval,
                    published=source.published,
                    enabled=source.enabled,
                )
                for source in knowledge_sources
            )
            bindable_tools = tuple(
                SnapshotTool(
                    tool_id=tool.tool_id,
                    version=tool.version,
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.input_schema,
                    published=tool.published,
                    enabled=tool.enabled,
                    source_available=tool.source_available,
                )
                for tool in tools
            )
            knowledge_tools = tuple(
                SnapshotTool.model_validate(source.model_dump(mode="json"))
                for source in snapshot_knowledge_sources
            )
            snapshot_tools = tuple(
                {
                    tool.tool_id: tool
                    for tool in (*bindable_tools, *knowledge_tools)
                }.values()
            )

        created_at = self.clock()
        payload = ExecutionSnapshotPayload(
            schema_version=schema_version,
            snapshot_id=str(uuid4()),
            run_id=run_id,
            unit_id=str(context["unit_id"]),
            project_id=str(context["project_id"]),
            user_id=str(context["user_id"]),
            actor=actor,
            model=snapshot_model,
            messages=tuple(
                SnapshotMessage(
                    id=message.id,
                    sequence=message.sequence,
                    role=message.role,
                    content=message.content,
                    created_at=message.created_at,
                )
                for message in self.conversation_repository.get_run_messages(run_id)
            ),
            skills=snapshot_skills,
            knowledge_sources=snapshot_knowledge_sources,
            tools=snapshot_tools,
            limits=SnapshotRuntimeLimits(
                snapshot_max_bytes=self.max_bytes,
                max_iterations=self.max_iterations,
                max_tool_calls=self.max_tool_calls,
                max_subagents=self.max_subagents,
                max_output_bytes=self.max_output_bytes,
            ),
            created_at=created_at,
        )
        serialized = canonical_snapshot_bytes(payload)
        if len(serialized) > self.max_bytes:
            raise ValueError(f"execution snapshot exceeds {self.max_bytes} bytes")
        digest = hashlib.sha256(serialized).hexdigest()
        row = RuntimeExecutionSnapshot(
            snapshot_id=payload.snapshot_id,
            run_id=run_id,
            digest=digest,
            payload=payload.model_dump(mode="json"),
            created_at=created_at,
            expires_at=None,
        )
        self.session.add(row)
        self.session.commit()
        return self._stored(row)
