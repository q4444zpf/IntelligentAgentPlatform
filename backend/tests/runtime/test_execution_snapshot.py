import hashlib
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.agents.schemas import AgentInfo
from app.db.base import Base
from app.collaboration.repository import TeamRepository
from app.runtime.execution_snapshot import (
    ExecutionSnapshotPayload,
    ExecutionSnapshotService,
    PublishedAgentSnapshot,
    PublishedTeamSnapshot,
    RuntimeExecutionSnapshot,
    SnapshotIntegrityError,
    SnapshotModelSelection,
    SnapshotRuntimeLimits,
    SnapshotSkill,
    SnapshotSkillFile,
    SnapshotTeamMember,
    SnapshotTool,
    canonical_snapshot_bytes,
    verify_snapshot_digest,
)


class StaticAgentService:
    def __init__(self, agent: AgentInfo, tools, knowledge_sources=()):
        self.agent = agent
        self.tool_service = StaticToolService(tools, knowledge_sources)
        self.skill_service = StaticSkillService()

    def get(self, agent_id: str) -> AgentInfo:
        assert agent_id == self.agent.id
        return self.agent


class StaticSkillService:
    def get(self, name: str):
        assert name == "forecast"
        return type("Skill", (), {
            "name": name,
            "description": "洪峰预测",
            "version": "1.2.0",
            "content": "---\\nname: forecast\\ndescription: 洪峰预测\\n---\\n使用已绑定的预测工具。",
            "source": "created",
            "enabled": True,
            "tags": ["water"],
            "metadata": {"runtime": "text"},
            "file_count": 1,
            "updated_at": datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
        })()


class StaticToolService:
    def __init__(self, tools, knowledge_sources=()):
        self.tools = tools
        self.knowledge_sources = list(knowledge_sources)

    def resolve_bindable(self, tool_ids):
        assert tool_ids == [tool.tool_id for tool in self.tools]
        return self.tools

    def resolve_knowledge_sources(self, tool_ids):
        assert tool_ids == [tool.tool_id for tool in self.knowledge_sources]
        return self.knowledge_sources


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


class KnowledgeTool(Tool):
    def __init__(self):
        super().__init__()
        self.tool_id = "knowledge.reservoir.manual"
        self.version = "7"
        self.name = "水库规程"
        self.description = "水库调度规程知识源"
        self.source = "knowledge"
        self.risk_level = "low"
        self.output_schema = {"type": "object"}
        self.source_resource_id = "reservoir-manual"
        self.source_capability_id = None
        self.requires_approval = False


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
        "skills": [{"name": "forecast"}],
        "tool_ids": [tool_id],
        "knowledge_source_ids": [],
        "knowledge_sources": [],
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
        knowledge_source_ids=["knowledge.reservoir.manual"],
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
        StaticAgentService(agent, [Tool()], [KnowledgeTool()]),
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
    assert "使用已绑定的预测工具" in first.payload.skills[0].content
    assert first.payload.schema_version == "3"
    assert first.payload.tools[0].tool_id == "mcp.water.level"
    assert first.payload.tools[0].version == "3"
    assert first.payload.tools[0].input_schema["properties"]["station"] == {
        "type": "string"
    }
    assert first.payload.knowledge_sources[0].tool_id == (
        "knowledge.reservoir.manual"
    )
    assert first.payload.knowledge_sources[0].source == "knowledge"
    assert {tool.tool_id for tool in first.payload.tools} == {
        "mcp.water.level",
        "knowledge.reservoir.manual",
    }
    assert first.payload.limits.max_iterations == 4
    assert first.payload.limits.max_tool_calls == 8
    assert first.payload.limits.max_subagents == 4
    assert first.payload.limits.max_output_bytes == 4 * 1024 * 1024


def test_snapshot_digest_changes_when_skill_resource_digest_changes(snapshot_service):
    first = snapshot_service.create("run-1")
    skill = first.payload.skills[0]
    changed = SnapshotSkillFile(
        path="SKILL.md",
        size=1,
        sha256="f" * 64,
    )
    payload = first.payload.model_copy(
        update={"skills": (skill.model_copy(update={"files": (changed,)}),)}
    )

    assert canonical_snapshot_bytes(payload) != canonical_snapshot_bytes(first.payload)


def test_snapshot_captures_skill_manifest_and_attachments(snapshot_service):
    snapshot_service.agent_service.skill_service.read_files = lambda name: (
        ("SKILL.md", b"manifest"),
        ("references/rules.txt", b"rules"),
    )

    stored = snapshot_service.create("run-1")

    assert [item.path for item in stored.payload.skills[0].files] == [
        "SKILL.md",
        "references/rules.txt",
    ]
    assert stored.payload.skills[0].files[1].size == 5


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


def test_build_skill_context_returns_literal_messages_body_and_resource_index():
    from app.runtime.execution_snapshot import build_skill_context

    resource_index = (
        {
            "skill_name": "forecast",
            "path": "references/rules.txt",
            "size": 5,
            "sha256": "a" * 64,
        },
    )

    context = build_skill_context(
        system_prompt="System guidance.",
        context_prompt="Project context.",
        skills=(SnapshotSkill(
            name="forecast",
            content="Use the frozen forecast method.",
        ),),
        resource_index=resource_index,
    )

    assert context.skill_body == "Use the frozen forecast method."
    assert context.resource_index == resource_index
    assert context.system_messages == (
        {"role": "system", "content": "System guidance."},
        {
            "role": "system",
            "content": (
                "Project context.\n\n"
                "Skills: Use the frozen forecast method.\n\n"
                "Authorized Skill resources (read-only): "
                "[{\"path\": \"references/rules.txt\", \"sha256\": \""
                + "a" * 64
                + "\", \"size\": 5, \"skill_name\": \"forecast\"}]"
            ),
        },
    )


@pytest.mark.parametrize("availability", ["missing", "unpublished", "disabled"])
def test_snapshot_rejects_unavailable_bound_skills_with_stable_code(
    snapshot_service, availability
):
    class UnavailableSkillService:
        def get(self, name):
            assert name == "forecast"
            if availability == "missing":
                from app.skills.service import SkillNotFoundError

                raise SkillNotFoundError(name)
            return type("Skill", (), {
                "name": name,
                "description": "Forecast guidance",
                "version": "1",
                "content": "Use forecast guidance.",
                "source": "published",
                "enabled": availability != "disabled",
                "published": availability != "unpublished",
                "tags": [],
                "metadata": {},
                "file_count": 1,
                "updated_at": datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
                "skill_id": "skill-1",
                "version_id": "version-1",
            })()

    snapshot_service.agent_service.skill_service = UnavailableSkillService()

    with pytest.raises(SnapshotIntegrityError, match="^skill_unavailable$"):
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
        assert stored.payload.schema_version == "5"


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


@pytest.mark.parametrize(
    "mutation",
    [
        "responsibility",
        "position",
        "tool_whitelist",
        "member_count",
        "limits",
        "policy",
    ],
)
def test_team_snapshot_rejects_any_mirrored_state_drift(mutation):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        team, published = published_team_version(session)
        if mutation == "responsibility":
            published.members[1].responsibility = "tampered"
        elif mutation == "position":
            published.members[1].position = 7
        elif mutation == "tool_whitelist":
            published.members[1].tool_ids = []
        elif mutation == "member_count":
            session.delete(published.members[1])
        elif mutation == "limits":
            session.execute(
                update(type(published))
                .where(type(published).id == published.id)
                .values(max_steps=published.max_steps + 1)
            )
        else:
            session.execute(
                update(type(published))
                .where(type(published).id == published.id)
                .values(failure_strategy="continue_then_synthesize")
            )
        session.commit()
        session.expire_all()
        service = ExecutionSnapshotService(
            session,
            NoLiveAgentService(),
            TeamConversationRepository(published.id, actor_id=team.id),
        )

        with pytest.raises(SnapshotIntegrityError, match="Team .* mismatch"):
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


def test_schema_v4_team_snapshot_digest_vector_remains_frozen():
    definition = captured_agent_definition(
        "supervisor", prompt="p", model="m", tool_id="t"
    )
    legacy_definition = {
        key: value
        for key, value in definition.items()
        if key not in {"skills", "knowledge_sources"}
    }
    member = SnapshotTeamMember(
        agent_id="supervisor",
        role="supervisor",
        responsibility="r",
        agent_definition_digest=canonical_digest(legacy_definition),
        agent=PublishedAgentSnapshot(
            id="supervisor",
            name="n",
            description="d",
            runtime_form="common",
            language="zh-CN",
            system_prompt="p",
            context_prompt="c",
            approval_policy="never",
        ),
        model=SnapshotModelSelection(provider_id="provider-1", model="m"),
        skill_names=("forecast",),
        tool_ids=("t",),
        skills=(SnapshotSkill(name="forecast"),),
        tools=(
            SnapshotTool(
                tool_id="t",
                version="1",
                name="t",
                description="d",
                input_schema={"type": "object"},
                published=True,
                enabled=True,
                source_available=True,
            ),
        ),
    )
    payload = ExecutionSnapshotPayload(
        schema_version="4",
        snapshot_id="s",
        run_id="r",
        unit_id="u",
        project_id="p",
        user_id="x",
        actor=PublishedTeamSnapshot(
            id="team",
            version_id="version",
            version=1,
            definition_digest="a" * 64,
            supervisor=member,
            members=(),
            max_steps=4,
            max_parallel_members=1,
            timeout_seconds=60,
            failure_strategy="fail_fast",
            name="team",
            description="d",
            runtime_form="common",
            language="zh-CN",
            system_prompt="p",
            context_prompt="c",
            approval_policy="never",
        ),
        model=member.model,
        messages=(),
        skills=(SnapshotSkill(name="forecast"),),
        tools=member.tools,
        limits=SnapshotRuntimeLimits(
            snapshot_max_bytes=1048576,
            max_iterations=4,
            max_tool_calls=8,
            max_subagents=4,
            max_output_bytes=4194304,
        ),
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )

    assert hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest() == (
        "5ad4041dfb4eaff7fc1188ccdb54595ea71b73fc9084ad649cb31d5d6105be42"
    )
