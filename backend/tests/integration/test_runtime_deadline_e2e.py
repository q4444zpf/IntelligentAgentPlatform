import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.artifacts.service import ArtifactService
from app.audit.models import AuditEvent
from app.conversations.models import AgentRun, Conversation, Message, RunEvent
from app.conversations.repository import ConversationRepository
from app.runtime.checkpoint_store import (
    CheckpointStore,
    RuntimeCheckpoint,
    RuntimeRunnerRequest,
)
from app.runtime.execution_snapshot import (
    ExecutionSnapshotPayload,
    ExecutionSnapshotService,
    PublishedAgentSnapshot,
    PublishedTeamSnapshot,
    RuntimeExecutionSnapshot,
    SnapshotModelSelection,
    SnapshotRuntimeLimits,
    SnapshotTeamMember,
    StoredExecutionSnapshot,
    canonical_snapshot_bytes,
)
from app.runtime.launcher_api import create_launcher_app
from app.runtime.launcher_client import LauncherClient, LauncherHttpTransport
from app.runtime.run_lifecycle import SandboxRunCoordinator
from app.runtime.run_tokens import RunTokenService, RuntimeRunTokenRevocation
from app.runtime.runner_gateway_auth import (
    RunnerGatewayError,
    runner_gateway_error_handler,
)
from app.runtime.runner_gateway_router import create_router
from app.runtime.workflow_runner import (
    WorkflowRunnerClient,
    WorkflowRunnerHttpTransport,
)
from app.runtime.workflow_runner_api import create_runner_app
from app.runtime.sandbox_readiness import SandboxReadiness


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="requires PostgreSQL",
)

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_SIGNING_KEY = b"runtime-deadline-e2e-signing-key"
_LAUNCHER_TOKEN = "runtime-deadline-e2e-launcher-token"
_TEAM_TIMEOUT_SECONDS = 8
_CHILD_PROGRAM = r"""
import json
import os
from pathlib import Path

progress_path = Path(os.environ["IAP_E2E_PROGRESS_PATH"])


def mark(value):
    with progress_path.open("a", encoding="utf-8") as progress:
        progress.write(value + "\n")


mark("child-started")

from app.runtime import run_worker
from app.runtime.sandbox_runtime import SandboxRuntime

mark("child-imported")


class Graph:
    def __init__(self, content):
        self.content = content

    def invoke(self, state, *, config=None):
        mark(f"graph:{self.content}")
        return {
            **state,
            "messages": [
                *state["messages"],
                {"role": "assistant", "content": self.content},
            ],
            "status": "completed",
        }


class Factory:
    def __init__(self):
        self.supervisor_calls = 0

    def build(self, member, **_kwargs):
        if member.agent_id == "supervisor":
            self.supervisor_calls += 1
            if self.supervisor_calls == 1:
                return Graph(json.dumps({
                    "tasks": [{
                        "id": "forecast-task",
                        "member_id": "forecast",
                        "objective": "forecast",
                        "depends_on": [],
                        "position": 0,
                    }]
                }))
            return Graph("team synthesis")
        return Graph("forecast result")


run_worker.SandboxRuntime = lambda gateway: SandboxRuntime(
    gateway,
    agent_factory=Factory(),
)
mark("worker-main-started")
exit_code = run_worker.main()
mark(f"worker-main-returned:{exit_code}")
raise SystemExit(exit_code)
"""


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    database_url = os.environ.get("TEST_DATABASE_URL")
    if database_url is None:
        yield
        return
    subprocess.run(
        (
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "backend/alembic.ini",
            "upgrade",
            "head",
        ),
        check=True,
        env=os.environ | {"DATABASE_URL": database_url},
        timeout=60,
    )
    yield


class LiveServer:
    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(128)
        self.port = int(self.socket.getsockname()[1])
        self.server = uvicorn.Server(
            uvicorn.Config(
                app,
                log_level="warning",
                access_log=False,
                lifespan="off",
            )
        )
        self.thread = threading.Thread(
            target=self.server.run,
            kwargs={"sockets": [self.socket]},
            daemon=True,
        )

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started and self.thread.is_alive():
            if time.monotonic() >= deadline:
                raise TimeoutError("uvicorn test server did not start")
            time.sleep(0.01)
        if not self.thread.is_alive():
            raise RuntimeError("uvicorn test server stopped during startup")
        return self

    def __exit__(self, *_args) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.socket.close()
        if self.thread.is_alive():
            raise TimeoutError("uvicorn test server did not stop")


class ProcessBackedLauncher:
    def __init__(self, progress_path: Path) -> None:
        self.progress_path = progress_path
        self.processes: dict[str, subprocess.Popen[str]] = {}
        self.created_run_ids: list[str] = []
        self.terminate_run_ids: list[str] = []
        self.cleanup_run_ids: list[str] = []
        self._lock = threading.Lock()

    def create(self, run_id: str, payload: dict[str, str]) -> dict[str, str]:
        execution = {
            key: value
            for key, value in payload.items()
            if key != "workspace_path"
        }
        execution["run_id"] = run_id
        environment = os.environ.copy()
        environment["IAP_RUN_EXECUTION_REQUEST"] = json.dumps(execution)
        environment["IAP_E2E_PROGRESS_PATH"] = str(self.progress_path)
        existing_path = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = os.pathsep.join(
            value
            for value in (str(_BACKEND_ROOT), existing_path)
            if value
        )
        process = subprocess.Popen(
            [sys.executable, "-c", _CHILD_PROGRAM],
            cwd=_BACKEND_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        with self._lock:
            self.processes[run_id] = process
            self.created_run_ids.append(run_id)
        return {"run_id": run_id, "status": "created"}

    def inspect(self, run_id: str) -> dict[str, object]:
        process = self.processes.get(run_id)
        if process is None:
            raise KeyError(run_id)
        return_code = process.poll()
        if return_code is None:
            return {"run_id": run_id, "status": "running"}
        return {
            "run_id": run_id,
            "status": "exited",
            "exit_code": return_code,
            "oom_killed": False,
        }

    def terminate(self, run_id: str) -> dict[str, str]:
        self.terminate_run_ids.append(run_id)
        return {"run_id": run_id, "status": "terminated"}

    def cleanup(self, run_id: str) -> dict[str, str]:
        self.cleanup_run_ids.append(run_id)
        return {"run_id": run_id, "status": "cleaned"}

    def communicate(self, run_id: str) -> tuple[int, str, str]:
        process = self.processes[run_id]
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired as error:
            process.kill()
            stdout, stderr = process.communicate(timeout=5)
            progress = (
                self.progress_path.read_text(encoding="utf-8")
                if self.progress_path.exists()
                else "<missing>"
            )
            raise AssertionError(
                "child worker did not exit: "
                f"stdout={stdout!r}, stderr={stderr!r}, progress={progress!r}"
            ) from error
        return process.returncode, stdout, stderr

    def stop_all(self) -> None:
        for process in self.processes.values():
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


class DatabaseTeamSnapshotService:
    def __init__(
        self,
        session: Session,
        deadline_state: dict[str, datetime],
    ) -> None:
        self.session = session
        self.deadline_state = deadline_state
        self.reader = ExecutionSnapshotService(session, None, None)

    def create(self, run_id: str) -> StoredExecutionSnapshot:
        existing = self.reader.get_for_run(run_id)
        if existing is not None:
            return existing
        created_at = datetime.now(UTC)
        snapshot = _team_snapshot(run_id, created_at)
        self.session.add(
            RuntimeExecutionSnapshot(
                snapshot_id=snapshot.snapshot_id,
                run_id=snapshot.run_id,
                digest=snapshot.digest,
                payload=snapshot.payload.model_dump(mode="json"),
                created_at=snapshot.created_at,
                expires_at=None,
            )
        )
        self.session.flush()
        self.deadline_state["deadline"] = created_at + timedelta(
            seconds=_TEAM_TIMEOUT_SECONDS
        )
        return snapshot

    def get_for_run(self, run_id: str) -> StoredExecutionSnapshot | None:
        return self.reader.get_for_run(run_id)


class DeadlineCrossingArtifactService:
    def __init__(self, deadline_state: dict[str, datetime]) -> None:
        self.deadline_state = deadline_state
        self.artifact = SimpleNamespace(
            id="deadline-artifact",
            filename="deadline.txt",
            size_bytes=8,
            sha256=hashlib.sha256(b"deadline").hexdigest(),
            content_type="text/plain",
        )
        self.validation_started_at: datetime | None = None
        self.validation_finished_at: datetime | None = None

    def list_for_run(self, _run_id: str):
        release_at = self.deadline_state["deadline"] - timedelta(seconds=0.5)
        while datetime.now(UTC) < release_at:
            time.sleep(0.01)
        return [self.artifact]

    def get_for_run(self, _run_id: str, artifact_id: str):
        assert artifact_id == self.artifact.id
        self.validation_started_at = datetime.now(UTC)
        deadline = self.deadline_state["deadline"]
        release_at = deadline + timedelta(seconds=0.2)
        while datetime.now(UTC) < release_at:
            time.sleep(0.01)
        self.validation_finished_at = datetime.now(UTC)
        return self.artifact


def _team_snapshot(run_id: str, created_at: datetime) -> StoredExecutionSnapshot:
    def member(agent_id: str, role: str) -> SnapshotTeamMember:
        return SnapshotTeamMember(
            agent_id=agent_id,
            role=role,
            responsibility=role,
            agent_definition_digest=hashlib.sha256(
                agent_id.encode("utf-8")
            ).hexdigest(),
            agent=PublishedAgentSnapshot(
                id=agent_id,
                name=agent_id,
                description="",
                runtime_form="common",
                language="zh-CN",
                system_prompt="",
                context_prompt="",
                approval_policy="never",
            ),
            model=SnapshotModelSelection(
                provider_id=f"{agent_id}-provider",
                model=f"{agent_id}-model",
            ),
        )

    supervisor = member("supervisor", "supervisor")
    actor = PublishedTeamSnapshot(
        id="deadline-team",
        version_id="deadline-team-version",
        version=1,
        definition_digest="d" * 64,
        supervisor=supervisor,
        members=(member("forecast", "member"),),
        max_steps=1,
        max_parallel_members=1,
        timeout_seconds=_TEAM_TIMEOUT_SECONDS,
        failure_strategy="fail_fast",
        name="Deadline Team",
        description="",
        runtime_form="common",
        language="zh-CN",
        system_prompt="",
        context_prompt="",
        approval_policy="never",
    )
    payload = ExecutionSnapshotPayload(
        schema_version="5",
        snapshot_id=str(uuid.uuid4()),
        run_id=run_id,
        unit_id="deadline-unit",
        project_id="deadline-project",
        user_id="deadline-user",
        actor=actor,
        model=supervisor.model,
        messages=(),
        limits=SnapshotRuntimeLimits(snapshot_max_bytes=1_048_576),
        created_at=created_at,
    )
    return StoredExecutionSnapshot(
        snapshot_id=payload.snapshot_id,
        run_id=run_id,
        digest=hashlib.sha256(canonical_snapshot_bytes(payload)).hexdigest(),
        payload=payload,
        created_at=created_at,
        expires_at=None,
    )


def _gateway_app(
    factory: sessionmaker[Session],
    artifacts: DeadlineCrossingArtifactService,
) -> FastAPI:
    def database_session():
        with factory() as session:
            yield session

    def token_service(
        session: Session = Depends(database_session),
    ) -> RunTokenService:
        return RunTokenService(session, signing_key=_SIGNING_KEY)

    def snapshot_service(
        session: Session = Depends(database_session),
    ) -> ExecutionSnapshotService:
        return ExecutionSnapshotService(session, None, None)

    def checkpoint_store(
        session: Session = Depends(database_session),
    ) -> CheckpointStore:
        return CheckpointStore(session)

    def repository(
        session: Session = Depends(database_session),
    ) -> ConversationRepository:
        return ConversationRepository(session)

    def artifact_service() -> ArtifactService:
        return artifacts

    app = FastAPI()
    app.add_exception_handler(RunnerGatewayError, runner_gateway_error_handler)
    app.include_router(
        create_router(
            token_service_dependency=token_service,
            snapshot_service_dependency=snapshot_service,
            checkpoint_store_dependency=checkpoint_store,
            conversation_repository_dependency=repository,
            artifact_service_dependency=artifact_service,
        ),
        prefix="/internal/runner",
    )
    return app


def test_real_http_worker_late_success_cannot_beat_team_deadline(tmp_path):
    database_url = os.environ["TEST_DATABASE_URL"]
    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": 5,
            "options": "-c lock_timeout=7000 -c statement_timeout=10000",
        },
    )
    factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
        class_=Session,
    )
    run_id = str(uuid.uuid4())
    conversation_id = str(uuid.uuid4())
    message_id = str(uuid.uuid4())
    deadline_state: dict[str, datetime] = {}
    artifacts = DeadlineCrossingArtifactService(deadline_state)
    launcher = ProcessBackedLauncher(tmp_path / "child-progress.txt")

    with factory.begin() as seed:
        seed.add(
            Conversation(
                id=conversation_id,
                unit_id="deadline-unit",
                project_id="deadline-project",
                owner_id="deadline-user",
                title="Runtime deadline E2E",
            )
        )
        seed.flush()
        seed.add(
            Message(
                id=message_id,
                conversation_id=conversation_id,
                sequence=1,
                role="user",
                content="forecast",
            )
        )
        seed.flush()
        seed.add(
            AgentRun(
                id=run_id,
                conversation_id=conversation_id,
                trigger_message_id=message_id,
                actor_type="team",
                actor_id="deadline-team",
                actor_version_id="deadline-team-version",
                actor_roles_json=[],
                status="queued",
            )
        )

    try:
        with LiveServer(_gateway_app(factory, artifacts)) as gateway_server:
            with LiveServer(
                create_launcher_app(launcher, runner_token=_LAUNCHER_TOKEN)
            ) as launcher_server:
                launcher_client = LauncherClient(
                    LauncherHttpTransport(
                        launcher_server.url,
                        _LAUNCHER_TOKEN,
                    )
                )
                readiness = SandboxReadiness(
                    image_trusted=True,
                    non_root=True,
                    read_only_root=True,
                    runner_gateway_network=True,
                    resource_limits=True,
                    cleanup_guaranteed=True,
                )
                with LiveServer(
                    create_runner_app(
                        sandbox_enabled=True,
                        readiness=readiness,
                        launcher_client=launcher_client,
                    )
                ) as runner_server:
                    coordinator = SandboxRunCoordinator(
                        factory,
                        WorkflowRunnerClient(
                            WorkflowRunnerHttpTransport(runner_server.url)
                        ),
                        poll_interval=0.02,
                        timeout_seconds=20,
                        snapshot_service_factory=lambda session: (
                            DatabaseTeamSnapshotService(
                                session,
                                deadline_state,
                            )
                        ),
                        token_service_factory=lambda session: RunTokenService(
                            session,
                            signing_key=_SIGNING_KEY,
                        ),
                        gateway_url=(
                            f"{gateway_server.url}/internal/runner"
                        ),
                    )
                    coordinator.execute(run_id)
                    return_code, stdout, stderr = launcher.communicate(run_id)

        deadline = deadline_state["deadline"]
        progress = launcher.progress_path.read_text(encoding="utf-8")
        assert artifacts.validation_started_at is not None, (
            return_code,
            stdout,
            stderr,
            progress,
        )
        assert artifacts.validation_started_at < deadline
        assert artifacts.validation_finished_at is not None
        assert artifacts.validation_finished_at >= deadline
        assert launcher.created_run_ids == [run_id]
        assert launcher.cleanup_run_ids == [run_id]

        with factory() as verification:
            run = verification.get(AgentRun, run_id)
            events = list(
                verification.scalars(
                    select(RunEvent)
                    .where(RunEvent.run_id == run_id)
                    .order_by(RunEvent.sequence)
                )
            )
            assert run.status == "failed"
            assert any(
                (
                    event.event_type == "run.error"
                    and event.payload.get("code") == "sandbox_timeout"
                )
                or (
                    event.event_type == "runner.completion"
                    and event.payload.get("status") == "failed"
                    and event.payload.get("error_code") == "sandbox_timeout"
                )
                for event in events
            )
            assert not any(
                event.event_type == "runner.completion"
                and event.payload.get("status") == "completed"
                for event in events
            )
        assert return_code == 4, (stdout, stderr)
    finally:
        launcher.stop_all()
        with factory.begin() as cleanup:
            cleanup.execute(
                delete(RuntimeRunnerRequest).where(
                    RuntimeRunnerRequest.run_id == run_id
                )
            )
            cleanup.execute(
                delete(RuntimeCheckpoint).where(
                    RuntimeCheckpoint.run_id == run_id
                )
            )
            cleanup.execute(
                delete(RuntimeRunTokenRevocation).where(
                    RuntimeRunTokenRevocation.run_id == run_id
                )
            )
            cleanup.execute(
                delete(RuntimeExecutionSnapshot).where(
                    RuntimeExecutionSnapshot.run_id == run_id
                )
            )
            cleanup.execute(delete(AuditEvent).where(AuditEvent.run_id == run_id))
            cleanup.execute(delete(RunEvent).where(RunEvent.run_id == run_id))
            cleanup.execute(delete(AgentRun).where(AgentRun.id == run_id))
            cleanup.execute(delete(Message).where(Message.id == message_id))
            cleanup.execute(
                delete(Conversation).where(Conversation.id == conversation_id)
            )
        engine.dispose()
