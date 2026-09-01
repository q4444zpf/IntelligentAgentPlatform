import hashlib
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.agents.schemas import AgentInfo
from app.db.base import Base
from app.collaboration.repository import TeamRepository
from app.runtime.execution_snapshot import (
    ExecutionSnapshotPayload,
    ExecutionSnapshotService,
    PublishedAgentSnapshot,
    RuntimeExecutionSnapshot,
    SnapshotIntegrityError,
    SnapshotModelSelection,
    SnapshotRuntimeLimits,
    canonical_snapshot_bytes,
    verify_snapshot_digest,
)


class StaticAgentService:
    def __init__(self, agent: AgentInfo, tools):
        self.agent = agent
        self.tool_service = StaticToolService(tools)

    def get(self, agent_id: str) -> AgentInfo:
        assert agent_id == self.agent.id
        return self.agent


class StaticToolService:
    def __init__(self, tools):
        self.tools = tools

    def resolve_bindable(self, tool_ids):
        assert tool_ids == [tool.tool_id for tool in self.tools]
        return self.tools


class Tool:
    def __init__(self):
        self.tool_id = "mcp.water.level"
        self.version = "3"
        self.name = "查询水位"
        self.description = "查询测站水位"
        self.input_schema = {
            "type": "object",
            "properties": {"station": {"type": "string"}},
        }
        self.published = True
        self.enabled = True
        self.source_available = True


class Run:
    def __init__(
        self,
        actor_id: str,
        *,
        actor_type: str = "agent",
        actor_version_id: str | None = None,
    ):
        self.actor_id = actor_id
        self.actor_type = actor_type
        self.actor_version_id = actor_version_id


class Message:
    def __init__(self, message_id: str, sequence: int, role: str, content: str):
        self.id = message_id
        self.sequence = sequence
        self.role = role
        self.content = content
        self.created_at = datetime(2026, 8, 14, 10, 0, tzinfo=UTC)


class StaticConversationRepository:
    def get_run_execution_context(self, run_id: str) -> dict[str, object] | None:
        if run_id != "run-1":
            return None
        return {
            "run_id": run_id,
            "unit_id": "unit-1",
            "project_id": "project-1",
            "user_id": "user-1",
            "actor_roles": ("operator",),
        }

    def get_run_by_id(self, run_id: str) -> Run | None:
        return Run("agent-1") if run_id == "run-1" else None

    def get_run_messages(self, run_id: str) -> list[Message]:
        if run_id != "run-1":
            return []
        return [Message("message-1", 1, "user", "水位是多少？")]


class TeamConversationRepository(StaticConversationRepository):
    def __init__(self, version_id, *, actor_id="team-1", project_id="project-1"):
        self.version_id = version_id
        self.actor_id = actor_id
        self.project_id = project_id

    def get_run_execution_context(self, run_id):
        if run_id != "run-team":
            return None
        return {
            "run_id": run_id,
            "unit_id": "unit-1",
            "project_id": self.project_id,
            "user_id": "user-1",
            "actor_roles": ("operator",),
        }

    def get_run_by_id(self, run_id):
        if run_id != "run-team":
            return None
        return Run(
            self.actor_id,
            actor_type="team",
            actor_version_id=self.version_id,
        )

    def get_run_messages(self, run_id):
        return [Message("team-message-1", 1, "user", "联合研判")]


class NoLiveAgentService:
    def get(self, agent_id):
        raise AssertionError(f"live AgentService.get called for {agent_id}")


def canonical_digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def captured_agent_definition(agent_id, *, prompt, model, tool_id):
    return {
        "id": agent_id,
        "name": f"{agent_id} name",
        "description": f"{agent_id} description",
        "runtime_form": "common",
        "language": "zh-CN",
        "provider_id": "provider-1",
        "model": model,
        "system_prompt": prompt,
        "context_prompt": f"{agent_id} context",
        "approval_policy": "control_commands",
        "skill_names": ["forecast"],
        "tool_ids": [tool_id],
        "knowledge_source_ids": [],
        "tools": [
            {
                "tool_id": tool_id,
                "version": "3",
                "name": tool_id,
                "description": f"{tool_id} description",
                "input_schema": {"type": "object"},
                "published": True,
                "enabled": True,
                "source_available": True,
            }
        ],
    }


def published_team_version(session):
    repository = TeamRepository(session)
    team = repository.create(
        unit_id="unit-1",
        project_id="project-1",
        name="联合研判",
        created_by="user-1",
    )
    supervisor = captured_agent_definition(
        "supervisor", prompt="original supervisor prompt", model="supervisor-v1",
        tool_id="forecast.read",
    )
    member = captured_agent_definition(
        "member", prompt="original member prompt", model="member-v1",
        tool_id="review.read",
    )
    definition = {
        "name": "联合研判",
        "description": "immutable team",
        "supervisor": {
            "agent_id": "supervisor",
            "agent_definition_digest": canonical_digest(supervisor),
            "agent_definition": supervisor,
            "responsibility": "coordinate",
            "tool_ids": ["forecast.read"],
            "skill_names": ["forecast"],
            "knowledge_source_ids": [],
        },
        "members": [
            {
                "agent_id": "member",
                "agent_definition_digest": canonical_digest(member),
                "agent_definition": member,
                "responsibility": "review",
                "tool_ids": ["review.read"],
                "skill_names": ["forecast"],
                "knowledge_source_ids": [],
            }
        ],
        "tool_ids": ["forecast.read", "review.read"],
        "skill_names": ["forecast"],
        "knowledge_source_ids": [],
        "max_steps": 4,
        "max_parallel_members": 1,
        "timeout_seconds": 60,
        "failure_strategy": "fail_fast",
        "approval_policy_id": None,
    }
    repository.save_draft(
        team.id, expected_revision=1, definition=definition, updated_by="user-1"
    )
    stored_definition = repository.get_version(team.id, 0).definition
    published = repository.publish(
        team.id,
        expected_revision=2,
        definition=stored_definition,
        definition_digest=canonical_digest(stored_definition),
        published_by="user-1",
    )
    session.commit()
    return team, published


@pytest.fixture
def snapshot_service():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    agent = AgentInfo(
        id="agent-1",
        name="Water Agent",
        description="Published water agent",
        runtime_form="web",
        language="zh-CN",
        provider_id="provider-1",
        model="water-model-1",
        system_prompt="Answer from published context.",
        context_prompt="Use run context.",
        approval_policy="control_commands",
        skill_names=["forecast"],
        tool_ids=["mcp.water.level"],
        enabled=True,
        pinned=False,
        is_builtin=False,
        is_default=False,
        startup_status="ready",
        workspace_dir="/workspace/agent-1",
        created_at=datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
        updated_at=datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
    )
    yield ExecutionSnapshotService(
        Session(engine),
        StaticAgentService(agent, [Tool()]),
        StaticConversationRepository(),
        clock=lambda: datetime(2026, 8, 14, 10, 1, tzinfo=UTC),
    )
    engine.dispose()


def test_snapshot_digest_is_deterministic_and_covers_complete_payload(snapshot_service):
    first = snapshot_service.create("run-1")
    second_bytes = canonical_snapshot_bytes(first.payload)

    assert hashlib.sha256(second_bytes).hexdigest() == first.digest
    assert verify_snapshot_digest(first.payload, first.digest)
    assert snapshot_service.get(first.snapshot_id) == first
    assert first.payload.actor.id == "agent-1"
    assert first.payload.messages[0].content == "水位是多少？"
    assert first.payload.skills[0].name == "forecast"
    assert first.payload.schema_version == "3"
    assert first.payload.tools[0].tool_id == "mcp.water.level"
    assert first.payload.tools[0].version == "3"
    assert first.payload.tools[0].input_schema["properties"]["station"] == {
        "type": "string"
    }
    assert first.payload.limits.max_iterations == 4
    assert first.payload.limits.max_tool_calls == 8
    assert first.payload.limits.max_subagents == 4
    assert first.payload.limits.max_output_bytes == 4 * 1024 * 1024


def test_snapshot_contains_no_provider_or_mcp_secrets(snapshot_service):
    stored = snapshot_service.create("run-1")
    serialized = canonical_snapshot_bytes(stored.payload).decode("utf-8")

    assert "provider-secret" not in serialized
    assert "mcp-secret" not in serialized
    assert '"provider_id":"provider-1"' in serialized
    assert '"tool_id":"mcp.water.level"' in serialized


def test_snapshot_rejects_disabled_agents(snapshot_service):
    snapshot_service.agent_service.agent.enabled = False

    with pytest.raises(ValueError, match="Agent 'agent-1' is disabled"):
        snapshot_service.create("run-1")


def test_team_snapshot_uses_only_captured_agent_definitions():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        team, published = published_team_version(session)
        service = ExecutionSnapshotService(
            session,
            NoLiveAgentService(),
            TeamConversationRepository(published.id, actor_id=team.id),
            clock=lambda: datetime(2026, 9, 1, tzinfo=UTC),
        )

        stored = service.create("run-team")

        actor = stored.payload.actor
        assert actor.kind == "team"
        assert actor.supervisor.agent.system_prompt == "original supervisor prompt"
        assert actor.supervisor.model.model == "supervisor-v1"
        assert actor.supervisor.tools[0].tool_id == "forecast.read"
        assert actor.members[0].agent.system_prompt == "original member prompt"
        assert actor.members[0].model.model == "member-v1"
        assert actor.members[0].tools[0].tool_id == "review.read"


@pytest.mark.parametrize(
    ("actor_id", "project_id"),
    [("other-team", "project-1"), ("team-1", "other-project")],
)
def test_team_snapshot_rejects_selected_version_outside_run_actor_scope(
    actor_id, project_id
):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        team, published = published_team_version(session)
        selected_actor = team.id if actor_id == "team-1" else actor_id
        service = ExecutionSnapshotService(
            session,
            NoLiveAgentService(),
            TeamConversationRepository(
                published.id, actor_id=selected_actor, project_id=project_id
            ),
        )

        with pytest.raises(ValueError, match="Selected Team version is unavailable"):
            service.create("run-team")


def test_team_snapshot_rejects_tampered_member_definition_digest():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        team, published = published_team_version(session)
        published.members[0].agent_definition_digest = "f" * 64
        session.commit()
        service = ExecutionSnapshotService(
            session,
            NoLiveAgentService(),
            TeamConversationRepository(published.id, actor_id=team.id),
        )

        with pytest.raises(SnapshotIntegrityError, match="Agent definition digest"):
            service.create("run-team")


def test_snapshot_rejects_payloads_larger_than_configured_limit(snapshot_service):
    snapshot_service.max_bytes = 1

    with pytest.raises(ValueError, match="execution snapshot exceeds 1 bytes"):
        snapshot_service.create("run-1")


def test_snapshot_get_rejects_payload_tampered_after_persistence(snapshot_service):
    stored = snapshot_service.create("run-1")
    row = snapshot_service.session.get(RuntimeExecutionSnapshot, stored.snapshot_id)
    row.payload = {**row.payload, "user_id": "attacker"}
    snapshot_service.session.commit()

    with pytest.raises(SnapshotIntegrityError, match="digest mismatch"):
        snapshot_service.get(stored.snapshot_id)


@pytest.mark.parametrize(
    ("schema_version", "expected_digest"),
    [
        ("1", "ff79e119bc3c1ace31ea2ba6283546413f5b1e6dd7e9cf169900bf9deaa7d93f"),
        ("2", "c4cadfabbfbe2ef5dbd8026c792263232d7986a6395cd071b65e011553f6c8fc"),
    ],
)
def test_legacy_snapshot_digest_vectors_ignore_v3_runtime_limits(
    schema_version,
    expected_digest,
):
    payload = ExecutionSnapshotPayload(
        schema_version=schema_version,
        snapshot_id="legacy-snapshot",
        run_id="legacy-run",
        unit_id="unit-1",
        project_id="project-1",
        user_id="user-1",
        actor=PublishedAgentSnapshot(
            id="agent-1",
            name="Agent",
            description="",
            runtime_form="common",
            language="zh-CN",
            system_prompt="",
            context_prompt="",
            approval_policy="never",
        ),
        model=SnapshotModelSelection(provider_id="provider-1", model="model-1"),
        messages=(),
        limits=SnapshotRuntimeLimits(
            snapshot_max_bytes=1048576,
            max_iterations=99,
            max_tool_calls=77,
            max_subagents=55,
            max_output_bytes=33,
        ),
        created_at=datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
    )

    assert hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest() == expected_digest
