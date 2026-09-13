from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.agents.schemas import AgentInfo, SkillBinding
from app.db.base import Base
from app.runtime.execution_snapshot import (
    ExecutionSnapshotService,
    SkillUnavailableError,
)
from app.skills.models import Skill, SkillVersion


class ConversationRepository:
    def get_run_execution_context(self, run_id):
        return {
            "unit_id": "unit-1",
            "project_id": "project-1",
            "user_id": "user-1",
            "actor_roles": (),
        }

    def get_run_by_id(self, run_id):
        return SimpleNamespace(actor_id="agent-1", actor_type="agent")

    def get_run_messages(self, run_id):
        return []


class AgentService:
    def __init__(self, bindings, names):
        self.agent = AgentInfo(
            id="agent-1",
            name="Agent",
            description="",
            runtime_form="common",
            language="zh-CN",
            provider_id="provider",
            model="model",
            system_prompt="system",
            context_prompt="context",
            approval_policy="never",
            skill_names=names,
            skill_bindings=bindings,
            tool_ids=[],
            knowledge_source_ids=[],
            enabled=True,
            pinned=False,
            is_builtin=False,
            is_default=False,
            startup_status="ready",
            workspace_dir="/workspace/agent-1",
            created_at=datetime(2026, 9, 13, tzinfo=UTC),
            updated_at=datetime(2026, 9, 13, tzinfo=UTC),
        )
        self.tool_service = SimpleNamespace(
            resolve_bindable=lambda ids: [],
            resolve_knowledge_sources=lambda ids: [],
        )

    def get(self, agent_id):
        return self.agent


def _published_skill(session, *, skill_id, first_version_id, second_version_id):
    skill = Skill(
        id=skill_id,
        unit_id="unit-1",
        project_id="project-1",
        name="forecast",
        created_by="user-1",
    )
    session.add(skill)
    session.flush()
    first = SkillVersion(
        id=first_version_id,
        skill_id=skill_id,
        version=1,
        source_revision=1,
        idempotency_key="publish-v1",
        request_digest="a" * 64,
        published_by="user-1",
        name="forecast",
        description="v1",
        display_version="1.0",
        content="Use forecast version one.",
        files=[],
        package_digest="b" * 64,
        object_key=f"unit-1/project-1/{skill_id}/v1.zip",
        archive_sha256="c" * 64,
        size_bytes=1,
    )
    second = SkillVersion(
        id=second_version_id,
        skill_id=skill_id,
        version=2,
        source_revision=2,
        idempotency_key="publish-v2",
        request_digest="d" * 64,
        published_by="user-1",
        name="forecast",
        description="v2",
        display_version="2.0",
        content="Use forecast version two.",
        files=[],
        package_digest="e" * 64,
        object_key=f"unit-1/project-1/{skill_id}/v2.zip",
        archive_sha256="f" * 64,
        size_bytes=1,
    )
    session.add_all((first, second))
    session.flush()
    skill.published_version_id = second_version_id
    session.commit()


def test_snapshot_reads_the_agent_bound_immutable_skill_version_after_newer_publish():
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    skill_id = "00000000-0000-0000-0000-000000000001"
    first_version_id = "00000000-0000-0000-0000-000000000011"
    second_version_id = "00000000-0000-0000-0000-000000000012"
    with Session(engine) as session:
        _published_skill(
            session,
            skill_id=skill_id,
            first_version_id=first_version_id,
            second_version_id=second_version_id,
        )
        service = ExecutionSnapshotService(
            session,
            AgentService(
                (SkillBinding(
                    skill_id=skill_id,
                    version_id=first_version_id,
                    name="forecast",
                ),),
                ["forecast"],
            ),
            ConversationRepository(),
        )

        snapshot = service.create("run-1")

    assert snapshot.payload.skills[0].version_id == first_version_id
    assert snapshot.payload.skills[0].content == "Use forecast version one."


def test_snapshot_rejects_name_only_legacy_agent_skill_binding():
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        service = ExecutionSnapshotService(
            session,
            AgentService((), ["forecast"]),
            ConversationRepository(),
        )

        with pytest.raises(SkillUnavailableError, match="^skill_unavailable$"):
            service.create("run-1")
