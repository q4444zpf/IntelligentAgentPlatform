import logging
import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.agents.service import AgentService
from app.agents.store import AgentStore
from app.approvals.models import Approval
from app.artifacts.storage import S3ObjectStorage
from app.core.config import RunnerTokenSettings
from app.core.database import SessionFactory
from app.identity.authorization import AuthorizationService
from app.identity.repository import AuthorizationRepository
from app.identity.schemas import ResourceScope
from app.mcp.protocol import McpProtocolClient
from app.mcp.store import McpStore
from app.model_providers.store import ProviderStore
from app.runtime.checkpoint_store import CheckpointStore
from app.runtime.execution_snapshot import (
    ExecutionSnapshotService,
    SnapshotIntegrityError,
    team_execution_deadline,
)
from app.runtime.harness import PlatformAgentHarness
from app.runtime.model_gateway import ModelGateway, OpenAICompatibleModelGateway
from app.runtime.run_lifecycle import SandboxRunCoordinator
from app.runtime.run_tokens import RunTokenService
from app.runtime.workflow_runner import workflow_runner_client_from_env
from app.tools.gateway import ToolGateway
from app.tools.schemas import ToolExecutionContext, ToolRuntimeError
from app.tools.service import ToolService
from app.tools.store import ToolStore

from .models import AgentRun, ToolInvocation
from .repository import ConversationRepository

logger = logging.getLogger(__name__)


def _execute_approved_tool(
    session_factory: sessionmaker[Session], approval_id: str
) -> str | None:
    with session_factory() as session:
        repository = ConversationRepository(session)
        approval = session.get(Approval, approval_id)
        if approval is None or approval.status != "approved":
            return None
        run_id = approval.run_id
        run = session.scalar(
            select(AgentRun).where(AgentRun.id == run_id).with_for_update()
        )
        if run is None or run.status != "queued":
            return None
        invocation = session.scalar(
            select(ToolInvocation)
            .where(ToolInvocation.id == approval.invocation_id)
            .with_for_update()
        )
        if invocation is None or invocation.status != "waiting_approval":
            return None
        context_data = repository.get_run_execution_context(run_id)
        if context_data is None:
            return None
        if invocation.tool_id.startswith("skill.") and ".script." in invocation.tool_id:
            # The resumed sandbox re-enters script admission after approval.
            return run_id
        gateway = ToolGateway(
            tool_store=ToolStore(session_factory),
            repository=repository,
            mcp_store=McpStore(session_factory),
            mcp_protocol_client=McpProtocolClient(),
        )
        try:
            if run.actor_type == "team":
                try:
                    snapshot = ExecutionSnapshotService(
                        session, None, repository
                    ).get_for_run(run_id)
                except SnapshotIntegrityError as error:
                    raise ToolRuntimeError(
                        "sandbox_timeout", "沙箱任务执行超时"
                    ) from error
                deadline = (
                    team_execution_deadline(snapshot)
                    if snapshot is not None
                    else None
                )
                if deadline is None or datetime.now(UTC) >= deadline:
                    raise ToolRuntimeError("sandbox_timeout", "沙箱任务执行超时")
            actor_roles = tuple(context_data["actor_roles"])
            if run.actor_type == "team":
                try:
                    authorization = AuthorizationRepository(
                        session
                    ).load_current_context(
                        str(context_data["user_id"]),
                        str(context_data["unit_id"]),
                        str(context_data["project_id"]),
                    )
                except LookupError as error:
                    raise ToolRuntimeError(
                        "tool_not_authorized", "该工具当前不可用。"
                    ) from error
                if not AuthorizationService().allows(
                    authorization,
                    "tool.invoke",
                    ResourceScope(
                        str(context_data["unit_id"]),
                        str(context_data["project_id"]),
                        str(context_data["user_id"]),
                    ),
                ):
                    raise ToolRuntimeError(
                        "tool_not_authorized", "该工具当前不可用。"
                    )
                actor_roles = authorization.role_codes
            context = ToolExecutionContext(
                unit_id=context_data["unit_id"],
                run_id=run_id,
                conversation_id=context_data["conversation_id"],
                project_id=context_data["project_id"],
                user_id=context_data["user_id"],
                actor_roles=actor_roles,
            )
            gateway.execute_approved(approval_id, context)
        except ToolRuntimeError as error:
            transition = session.execute(
                update(AgentRun)
                .where(
                    AgentRun.id == run_id,
                    AgentRun.status.not_in({"completed", "failed", "cancelled"}),
                )
                .values(status="failed")
            )
            if transition.rowcount:
                repository.append_event(
                    run_id,
                    "run.error",
                    {"code": error.code, "message": error.safe_message},
                )
                repository.append_event(run_id, "run.status", {"status": "failed"})
                session.commit()
            return None
        run = session.scalar(
            select(AgentRun).where(AgentRun.id == run_id).with_for_update()
        )
        if run is None or run.status in {"completed", "failed", "cancelled"}:
            return None
        return run_id


class RunDispatcher(ABC):
    @abstractmethod
    def dispatch(self, run_id: str) -> None:
        raise NotImplementedError

    def resume_approval(self, approval_id: str) -> None:
        return None

    def cancel(self, run_id: str) -> None:
        return None

    def recover(self, run_id: str) -> None:
        return None


class UnavailableRunDispatcher(RunDispatcher):
    def dispatch(self, run_id: str) -> None:
        return None

    def resume_approval(self, approval_id: str) -> None:
        return None


class ThreadRunDispatcher(RunDispatcher):
    def __init__(
        self,
        session_factory: sessionmaker[Session] = SessionFactory,
        gateway_factory: Callable[[], ModelGateway] | None = None,
        agent_service_factory: Callable[[], AgentService] | None = None,
        artifact_storage_factory: Callable[[], S3ObjectStorage] | None = None,
        max_workers: int = 4,
    ):
        self.session_factory = session_factory
        self.gateway_factory = gateway_factory or (
            lambda: OpenAICompatibleModelGateway(
                ProviderStore(self.session_factory)
            )
        )
        self.agent_service_factory = agent_service_factory or (
            lambda: AgentService(AgentStore(self.session_factory))
        )
        self.artifact_storage_factory = artifact_storage_factory or (lambda: None)
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="agent-run",
        )

    def dispatch(self, run_id: str) -> None:
        future = self.executor.submit(self._execute, run_id)
        future.add_done_callback(self._report_failure)

    def resume_approval(self, approval_id: str) -> None:
        future = self.executor.submit(self._resume_approval, approval_id)
        future.add_done_callback(self._report_failure)

    def _execute(self, run_id: str) -> None:
        with self.session_factory() as session:
            repository = ConversationRepository(session)
            tool_store = ToolStore(self.session_factory)
            tool_service = ToolService(tool_store)
            agent_service = (
                self.agent_service_factory()
                if self.agent_service_factory is not None
                else AgentService(
                    AgentStore(self.session_factory),
                    tool_service=tool_service,
                )
            )
            harness_tool_service = tool_service
            try:
                preview = agent_service.get(repository.get_run_by_id(run_id).actor_id)
                if not getattr(preview, "tool_ids", None):
                    harness_tool_service = None
            except Exception:  # noqa: BLE001, S110
                pass
            PlatformAgentHarness(
                repository,
                self.gateway_factory(),
                agent_service,
                tool_service=harness_tool_service,
                tool_gateway=ToolGateway(
                    tool_store=tool_store,
                    repository=repository,
                    mcp_store=McpStore(self.session_factory),
                    mcp_protocol_client=McpProtocolClient(),
                ),
                artifact_storage=self.artifact_storage_factory(),
                checkpoint_store=CheckpointStore(session),
                execution_snapshot_service=ExecutionSnapshotService(
                    session,
                    agent_service,
                    repository,
                ),
            ).execute(run_id)

    def _resume_approval(self, approval_id: str) -> None:
        run_id = _execute_approved_tool(self.session_factory, approval_id)
        if run_id is not None:
            self._execute(run_id)

    @staticmethod
    def _report_failure(future: Future[None]) -> None:
        if future.cancelled():
            return
        error = future.exception()
        if error is not None:
            logger.exception(
                "Agent run background task failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    def shutdown(
        self, *, wait: bool = True, cancel_futures: bool = False
    ) -> None:
        self.executor.shutdown(wait=wait, cancel_futures=cancel_futures)


class SandboxRunDispatcher(RunDispatcher):
    def __init__(
        self,
        coordinator: SandboxRunCoordinator,
        max_workers: int = 4,
        *,
        session_factory: sessionmaker[Session] = SessionFactory,
        recover_on_startup: bool = False,
    ):
        self.coordinator = coordinator
        self.session_factory = session_factory
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="sandbox-run")
        if recover_on_startup:
            for run_id in coordinator.list_recoverable_run_ids():
                self.recover(run_id)
            for run_id in coordinator.list_cleanup_retry_run_ids():
                future = self.executor.submit(coordinator.retry_cleanup, run_id)
                future.add_done_callback(ThreadRunDispatcher._report_failure)

    def dispatch(self, run_id: str) -> None:
        future = self.executor.submit(self.coordinator.execute, run_id)
        future.add_done_callback(ThreadRunDispatcher._report_failure)

    def shutdown(self, *, wait: bool = True, cancel_futures: bool = False) -> None:
        self.executor.shutdown(wait=wait, cancel_futures=cancel_futures)

    def cancel(self, run_id: str) -> None:
        self.coordinator.cancel(run_id)

    def resume_approval(self, approval_id: str) -> None:
        future = self.executor.submit(self._resume_approval, approval_id)
        future.add_done_callback(ThreadRunDispatcher._report_failure)

    def _resume_approval(self, approval_id: str) -> None:
        run_id = _execute_approved_tool(self.session_factory, approval_id)
        if run_id is not None:
            self.coordinator.execute(run_id)

    def recover(self, run_id: str) -> None:
        future = self.executor.submit(self.coordinator.recover, run_id)
        future.add_done_callback(ThreadRunDispatcher._report_failure)


def build_default_run_dispatcher(session_factory: sessionmaker[Session] = SessionFactory) -> RunDispatcher:
    runner = workflow_runner_client_from_env()
    if runner is None:
        return ThreadRunDispatcher(session_factory=session_factory)
    gateway_url = os.getenv("IAP_RUNNER_GATEWAY_URL", "").strip()
    if not gateway_url:
        raise ValueError("IAP_RUNNER_GATEWAY_URL is required for sandbox execution")
    token_settings = RunnerTokenSettings.from_env()
    agent_service = AgentService(AgentStore(session_factory))

    def snapshot_service(session: Session) -> ExecutionSnapshotService:
        return ExecutionSnapshotService(
            session,
            agent_service,
            ConversationRepository(session),
        )

    def token_service(session: Session) -> RunTokenService:
        return RunTokenService.from_settings(session, token_settings)

    return SandboxRunDispatcher(
        SandboxRunCoordinator(
            session_factory,
            runner,
            snapshot_service_factory=snapshot_service,
            token_service_factory=token_service,
            gateway_url=gateway_url,
        ),
        recover_on_startup=True,
    )
