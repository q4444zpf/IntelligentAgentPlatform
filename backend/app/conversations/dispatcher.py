from abc import ABC, abstractmethod
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
import logging

from sqlalchemy.orm import Session, sessionmaker

from app.agents.service import AgentService
from app.agents.store import AgentStore
from app.core.database import SessionFactory
from app.model_providers.store import ProviderStore
from app.runtime.harness import PlatformAgentHarness
from app.runtime.model_gateway import ModelGateway, OpenAICompatibleModelGateway
from app.tools.gateway import ToolGateway
from app.tools.service import ToolService
from app.tools.store import ToolStore
from app.mcp.protocol import McpProtocolClient
from app.mcp.store import McpStore
from app.approvals.models import Approval
from app.tools.schemas import ToolExecutionContext

from .repository import ConversationRepository

logger = logging.getLogger(__name__)


class RunDispatcher(ABC):
    @abstractmethod
    def dispatch(self, run_id: str) -> None:
        raise NotImplementedError

    def resume_approval(self, approval_id: str) -> None:
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
            except Exception:
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
            ).execute(run_id)

    def _resume_approval(self, approval_id: str) -> None:
        with self.session_factory() as session:
            repository = ConversationRepository(session)
            approval = session.get(Approval, approval_id)
            if approval is None:
                return
            run_id = approval.run_id
            context_data = repository.get_run_execution_context(approval.run_id)
            if context_data is None:
                return
            context = ToolExecutionContext(
                unit_id=context_data["unit_id"], run_id=approval.run_id,
                conversation_id=context_data["conversation_id"], project_id=context_data["project_id"],
                user_id=context_data["user_id"], actor_roles=context_data["actor_roles"],
            )
            tool_store = ToolStore(self.session_factory)
            gateway = ToolGateway(
                tool_store=tool_store,
                repository=repository,
                mcp_store=McpStore(self.session_factory),
                mcp_protocol_client=McpProtocolClient(),
            )
            gateway.execute_approved(approval_id, context)
            run = repository.get_run_by_id(approval.run_id)
            if run is not None:
                run.status = "queued"
            repository.append_event(approval.run_id, "approval.resolved", {"approval_id": approval_id, "status": "approved"})
            repository.append_event(approval.run_id, "run.status", {"status": "queued"})
            session.commit()
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
