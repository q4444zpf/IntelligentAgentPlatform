import json
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.agents.service import AgentService
from app.core.database import get_session
from app.core.request_context import RequestContext, require_request_context

from .dispatcher import ThreadRunDispatcher
from .repository import ConversationRepository
from .schemas import (
    AgentRunInfo,
    ConversationCreate,
    ConversationInfo,
    MessageAccepted,
    MessageCreate,
    MessageInfo,
    RunEventInfo,
    ToolInvocationInfo,
)
from .service import (
    AgentSelectionError,
    ConversationNotFound,
    ConversationService,
    RunNotFound,
)

ServiceFactory = Callable[[Session], ConversationService]

default_run_dispatcher = ThreadRunDispatcher()


def default_service_factory(session: Session) -> ConversationService:
    return ConversationService(
        ConversationRepository(session),
        default_run_dispatcher,
        agent_service=AgentService(),
    )


def encode_sse(event: RunEventInfo) -> str:
    data = json.dumps(event.payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.sequence}\nevent: {event.event_type}\ndata: {data}\n\n"


def create_router(
    service_factory: ServiceFactory = default_service_factory,
) -> APIRouter:
    router = APIRouter()

    def service(session: Session = Depends(get_session)) -> ConversationService:
        return service_factory(session)

    def not_found(operation: Callable[[], Any]) -> Any:
        try:
            return operation()
        except AgentSelectionError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except (ConversationNotFound, RunNotFound) as error:
            raise HTTPException(
                status_code=404, detail="Resource was not found"
            ) from error

    @router.post("/conversations", response_model=ConversationInfo, status_code=201)
    def create_conversation(
        request: ConversationCreate,
        context: RequestContext = Depends(require_request_context),
        manager: ConversationService = Depends(service),
    ):
        return manager.create_conversation(context, request)

    @router.get("/conversations", response_model=list[ConversationInfo])
    def list_conversations(
        context: RequestContext = Depends(require_request_context),
        manager: ConversationService = Depends(service),
    ):
        return manager.list_conversations(context)

    @router.get("/conversations/{conversation_id}", response_model=ConversationInfo)
    def get_conversation(
        conversation_id: str,
        context: RequestContext = Depends(require_request_context),
        manager: ConversationService = Depends(service),
    ):
        return not_found(lambda: manager.get_conversation(context, conversation_id))

    @router.get(
        "/conversations/{conversation_id}/messages", response_model=list[MessageInfo]
    )
    def list_messages(
        conversation_id: str,
        context: RequestContext = Depends(require_request_context),
        manager: ConversationService = Depends(service),
    ):
        return not_found(lambda: manager.list_messages(context, conversation_id))

    @router.post(
        "/conversations/{conversation_id}/messages",
        response_model=MessageAccepted,
        status_code=202,
    )
    def create_message(
        conversation_id: str,
        request: MessageCreate,
        context: RequestContext = Depends(require_request_context),
        manager: ConversationService = Depends(service),
    ):
        return not_found(
            lambda: manager.create_message(context, conversation_id, request)
        )

    @router.get("/agent-runs/{run_id}", response_model=AgentRunInfo)
    def get_run(
        run_id: str,
        context: RequestContext = Depends(require_request_context),
        manager: ConversationService = Depends(service),
    ):
        return not_found(lambda: manager.get_run(context, run_id))

    @router.get(
        "/agent-runs/{run_id}/tool-invocations",
        response_model=list[ToolInvocationInfo],
    )
    def list_tool_invocations(
        run_id: str,
        context: RequestContext = Depends(require_request_context),
        manager: ConversationService = Depends(service),
    ):
        return not_found(lambda: manager.list_tool_invocations(context, run_id))

    @router.get("/agent-runs/{run_id}/events")
    def get_events(
        run_id: str,
        last_event_id: Annotated[int, Header(alias="Last-Event-ID")] = 0,
        context: RequestContext = Depends(require_request_context),
        manager: ConversationService = Depends(service),
    ):
        events = not_found(
            lambda: manager.list_events(context, run_id, last_event_id)
        )
        return StreamingResponse(
            iter(encode_sse(event) for event in events),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    return router


router = create_router()
