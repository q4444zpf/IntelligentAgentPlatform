from datetime import UTC, datetime

from app.core.request_context import RequestContext

from .dispatcher import RunDispatcher
from .models import AgentRun, Conversation, Message, RunEvent
from .repository import ConversationRepository
from .schemas import (
    AgentRunInfo,
    ConversationCreate,
    ConversationInfo,
    MessageAccepted,
    MessageCreate,
    MessageInfo,
    RunEventInfo,
)


class ConversationNotFound(Exception):
    pass


class RunNotFound(Exception):
    pass


class ConversationService:
    def __init__(
        self, repository: ConversationRepository, dispatcher: RunDispatcher
    ):
        self.repository = repository
        self.dispatcher = dispatcher

    def create_conversation(
        self, context: RequestContext, request: ConversationCreate
    ) -> ConversationInfo:
        value = self.repository.add(
            Conversation(
                project_id=context.project_id,
                owner_id=context.user_id,
                title=request.title,
            )
        )
        self.repository.session.commit()
        return ConversationInfo.model_validate(value)

    def list_conversations(
        self, context: RequestContext
    ) -> list[ConversationInfo]:
        values = self.repository.list_conversations(
            context.project_id, context.user_id
        )
        return [ConversationInfo.model_validate(value) for value in values]

    def get_conversation(
        self, context: RequestContext, conversation_id: str
    ) -> ConversationInfo:
        value = self.repository.get_conversation(
            context.project_id, context.user_id, conversation_id
        )
        if value is None:
            raise ConversationNotFound(conversation_id)
        return ConversationInfo.model_validate(value)

    def list_messages(
        self, context: RequestContext, conversation_id: str
    ) -> list[MessageInfo]:
        if self.repository.get_conversation(
            context.project_id, context.user_id, conversation_id
        ) is None:
            raise ConversationNotFound(conversation_id)
        values = self.repository.list_messages(
            context.project_id, context.user_id, conversation_id
        )
        return [MessageInfo.model_validate(value) for value in values]

    def create_message(
        self,
        context: RequestContext,
        conversation_id: str,
        request: MessageCreate,
    ) -> MessageAccepted:
        conversation = self.repository.get_conversation(
            context.project_id, context.user_id, conversation_id
        )
        if conversation is None:
            raise ConversationNotFound(conversation_id)
        conversation.updated_at = datetime.now(UTC)
        message = self.repository.add(
            Message(
                conversation_id=conversation_id,
                role="user",
                content=request.content,
            )
        )
        run = self.repository.add(
            AgentRun(
                conversation_id=conversation_id,
                trigger_message_id=message.id,
                actor_type=request.actor_type,
                actor_id=request.actor_id,
                status="queued",
            )
        )
        self.repository.add(
            RunEvent(
                run_id=run.id,
                sequence=1,
                event_type="run.status",
                payload={"status": "queued"},
            )
        )
        self.repository.session.commit()
        self.dispatcher.dispatch(run.id)
        return MessageAccepted(
            message=MessageInfo.model_validate(message),
            run=AgentRunInfo.model_validate(run),
        )

    def get_run(self, context: RequestContext, run_id: str) -> AgentRunInfo:
        value = self.repository.get_run(
            context.project_id, context.user_id, run_id
        )
        if value is None:
            raise RunNotFound(run_id)
        return AgentRunInfo.model_validate(value)

    def list_events(
        self, context: RequestContext, run_id: str, after_sequence: int
    ) -> list[RunEventInfo]:
        if self.repository.get_run(
            context.project_id, context.user_id, run_id
        ) is None:
            raise RunNotFound(run_id)
        values = self.repository.list_events(run_id, after_sequence)
        return [RunEventInfo.model_validate(value) for value in values]
