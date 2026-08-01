from abc import ABC, abstractmethod
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
import logging

from sqlalchemy.orm import Session, sessionmaker

from app.core.database import SessionFactory
from app.model_providers.store import ProviderStore
from app.runtime.harness import PlatformAgentHarness
from app.runtime.model_gateway import ModelGateway, OpenAICompatibleModelGateway

from .repository import ConversationRepository

logger = logging.getLogger(__name__)


class RunDispatcher(ABC):
    @abstractmethod
    def dispatch(self, run_id: str) -> None:
        raise NotImplementedError


class UnavailableRunDispatcher(RunDispatcher):
    def dispatch(self, run_id: str) -> None:
        return None


class ThreadRunDispatcher(RunDispatcher):
    def __init__(
        self,
        session_factory: sessionmaker[Session] = SessionFactory,
        gateway_factory: Callable[[], ModelGateway] | None = None,
        max_workers: int = 4,
    ):
        self.session_factory = session_factory
        self.gateway_factory = gateway_factory or (
            lambda: OpenAICompatibleModelGateway(
                ProviderStore(self.session_factory)
            )
        )
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="agent-run",
        )

    def dispatch(self, run_id: str) -> None:
        future = self.executor.submit(self._execute, run_id)
        future.add_done_callback(self._report_failure)

    def _execute(self, run_id: str) -> None:
        with self.session_factory() as session:
            PlatformAgentHarness(
                ConversationRepository(session),
                self.gateway_factory(),
            ).execute(run_id)

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