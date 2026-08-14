from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
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


class SnapshotSkill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str


class SnapshotKnowledgeSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_id: str


class SnapshotTool(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_id: str
    version: str
    name: str
    description: str
    input_schema: dict[str, object]
    published: bool
    enabled: bool
    source_available: bool


class SnapshotRuntimeLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_max_bytes: int


class ExecutionSnapshotPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1", "2"] = "1"
    snapshot_id: str
    run_id: str
    unit_id: str
    project_id: str
    user_id: str
    actor: PublishedAgentSnapshot
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


def canonical_snapshot_bytes(payload: ExecutionSnapshotPayload) -> bytes:
    serialized = payload.model_dump(mode="json")
    if payload.schema_version == "1":
        serialized.pop("tools", None)
    return json.dumps(
        serialized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def verify_snapshot_digest(payload: ExecutionSnapshotPayload, digest: str) -> bool:
    expected = hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest()
    return hmac.compare_digest(expected, digest)


class ExecutionSnapshotService:
    def __init__(
        self,
        session: Session,
        agent_service,
        conversation_repository,
        *,
        max_bytes: int | None = None,
        clock=None,
    ):
        self.session = session
        self.agent_service = agent_service
        self.conversation_repository = conversation_repository
        self.max_bytes = max_bytes or int(
            os.getenv("IAP_RUNNER_SNAPSHOT_MAX_BYTES", "1048576")
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
        agent = self.agent_service.get(run.actor_id)
        if not agent.enabled:
            raise ValueError(f"Agent '{agent.id}' is disabled")

        created_at = self.clock()
        tools = self.agent_service.tool_service.resolve_bindable(agent.tool_ids)
        payload = ExecutionSnapshotPayload(
            schema_version="2",
            snapshot_id=str(uuid4()),
            run_id=run_id,
            unit_id=str(context["unit_id"]),
            project_id=str(context["project_id"]),
            user_id=str(context["user_id"]),
            actor=PublishedAgentSnapshot(
                id=agent.id,
                name=agent.name,
                description=agent.description,
                runtime_form=agent.runtime_form,
                language=agent.language,
                system_prompt=agent.system_prompt,
                context_prompt=agent.context_prompt,
                approval_policy=agent.approval_policy,
            ),
            model=SnapshotModelSelection(
                provider_id=agent.provider_id,
                model=agent.model,
            ),
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
            skills=tuple(SnapshotSkill(name=name) for name in agent.skill_names),
            knowledge_sources=tuple(
                SnapshotKnowledgeSource(tool_id=tool_id) for tool_id in agent.tool_ids
            ),
            tools=tuple(
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
            ),
            limits=SnapshotRuntimeLimits(snapshot_max_bytes=self.max_bytes),
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
