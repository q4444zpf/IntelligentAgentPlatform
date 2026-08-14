from datetime import UTC, datetime

import hashlib
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.agents.schemas import AgentInfo
from app.db.base import Base
from app.runtime.execution_snapshot import (
    ExecutionSnapshotService,
    RuntimeExecutionSnapshot,
    SnapshotIntegrityError,
    canonical_snapshot_bytes,
    verify_snapshot_digest,
)


class StaticAgentService:
    def __init__(self, agent: AgentInfo):
        self.agent = agent

    def get(self, agent_id: str) -> AgentInfo:
        assert agent_id == self.agent.id
        return self.agent


class Run:
    def __init__(self, actor_id: str):
        self.actor_id = actor_id


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
        StaticAgentService(agent),
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
