from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.audit.models import AuditEvent
from app.conversations.models import AgentRun, Conversation, Message, RunEvent, ToolInvocation
from app.conversations.repository import ConversationRepository
from app.db.base import Base
from app.runtime.run_lifecycle import SandboxRunCoordinator
from app.runtime.runner_gateway_auth import RunnerGatewayError
from app.runtime.runner_gateway_schemas import ScriptExecutionCompletionRequest
from app.runtime.runner_gateway_service import RunnerGatewayService
from tests.runtime.test_gateway_tools import FakeSnapshotService, FakeTokenService, build_snapshot
from tests.runtime.test_run_lifecycle import FakeTokens, SequenceRunner


@pytest.fixture
def script_factory(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'scripts.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add(Conversation(id="conversation-1", unit_id="unit-1", project_id="project-1", owner_id="user-1", title="scripts"))
        session.add(Message(id="message-1", conversation_id="conversation-1", role="user", content="run"))
        session.add(AgentRun(id="run-1", conversation_id="conversation-1", trigger_message_id="message-1", actor_type="agent", actor_id="agent-1", actor_roles_json=["user"], status="running"))
        session.add(ToolInvocation(id="lease-1", run_id="run-1", tool_call_id="call-1", tool_id="skill.forecast.script.normalize", tool_version="1", status="running", arguments_summary={}))
    yield factory
    engine.dispose()


def service_for(session):
    snapshot = build_snapshot()
    return RunnerGatewayService(FakeSnapshotService(snapshot), conversation_repository=ConversationRepository(session)), FakeTokenService(snapshot).verify("token", "run-1", "skill.script.execute")


@pytest.mark.parametrize("statuses", [("completed", "failed"), ("completed", "completed")])
def test_concurrent_callbacks_emit_one_immutable_terminal_record(script_factory, statuses):
    # Both requests retain a stale running lease, as concurrent ORM sessions can.
    ready = Barrier(2)

    def complete(index):
        with script_factory() as session:
            lease = session.get(ToolInvocation, "lease-1")
            assert lease.status == "running"
            ready.wait(timeout=5)
            service, claims = service_for(session)
            try:
                response = service.complete_script("run-1", "lease-1", ScriptExecutionCompletionRequest(status=statuses[index], duration_ms=10 + index), claims, f"completion-{index}")
                return response.status
            except RunnerGatewayError as error:
                assert error.code == "skill_script_already_completed"
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(complete, range(2)))
    with script_factory() as session:
        lease = session.get(ToolInvocation, "lease-1")
        events = list(session.scalars(select(RunEvent).where(RunEvent.event_type.like("skill.script.%"))))
        audits = list(session.scalars(select(AuditEvent).where(AuditEvent.action.like("skill.script.%"))))
        assert len(events) == len(audits) == 1
        assert events[0].event_type == audits[0].action == f"skill.script.{lease.status}"
        assert outcomes.count("conflict") == (0 if statuses[0] == statuses[1] else 1)
        assert lease.completed_at is not None


def test_user_cancel_persists_script_terminal_before_revocation_and_termination(script_factory):
    tokens = FakeTokens()

    class Runner(SequenceRunner):
        def terminate(self, run_id, **kwargs):
            with script_factory() as session:
                lease = session.get(ToolInvocation, "lease-1")
                assert lease.status == "cancelled"
                assert lease.error_code == "skill_script_cancelled"
                assert lease.completed_at is not None
                assert tokens.revoked == [("run-1", "sandbox_cancelled")]
            return super().terminate(run_id, **kwargs)

    runner = Runner([])
    coordinator = SandboxRunCoordinator(script_factory, runner, token_service_factory=lambda session: tokens)
    coordinator.cancel("run-1")
    coordinator.cancel("run-1")
    with script_factory() as session:
        events = list(session.scalars(select(RunEvent).where(RunEvent.event_type == "skill.script.cancelled")))
        audits = list(session.scalars(select(AuditEvent).where(AuditEvent.action == "skill.script.cancelled")))
        assert len(events) == len(audits) == 1
        assert events[0].payload["lease_id"] == audits[0].resource_id == "lease-1"
        service, claims = service_for(session)
        with pytest.raises(RunnerGatewayError) as failure:
            service.complete_script("run-1", "lease-1", ScriptExecutionCompletionRequest(status="completed", duration_ms=1), claims, "late-completion")
        assert failure.value.code == "skill_script_already_completed"
