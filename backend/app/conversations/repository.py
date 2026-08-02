from typing import TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import AgentRun, Conversation, Message, RunEvent, ToolInvocation

ModelT = TypeVar("ModelT", Conversation, Message, AgentRun, RunEvent, ToolInvocation)


class ConversationRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, value: ModelT) -> ModelT:
        self.session.add(value)
        self.session.flush()
        return value

    def list_conversations(
        self, project_id: str, owner_id: str
    ) -> list[Conversation]:
        query = (
            select(Conversation)
            .where(
                Conversation.project_id == project_id,
                Conversation.owner_id == owner_id,
                Conversation.archived_at.is_(None),
            )
            .order_by(Conversation.updated_at.desc())
        )
        return list(self.session.scalars(query))

    def get_conversation(
        self, project_id: str, owner_id: str, conversation_id: str
    ) -> Conversation | None:
        return self.session.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.project_id == project_id,
                Conversation.owner_id == owner_id,
            )
        )

    def get_run(
        self, project_id: str, owner_id: str, run_id: str
    ) -> AgentRun | None:
        return self.session.scalar(
            select(AgentRun)
            .join(Conversation)
            .where(
                AgentRun.id == run_id,
                Conversation.project_id == project_id,
                Conversation.owner_id == owner_id,
            )
        )

    def get_run_by_id(self, run_id: str) -> AgentRun | None:
        return self.session.get(AgentRun, run_id)

    def get_run_messages(self, run_id: str) -> list[Message]:
        run = self.get_run_by_id(run_id)
        if run is None:
            return []
        trigger = self.session.get(Message, run.trigger_message_id)
        if trigger is None:
            return []
        query = (
            select(Message)
            .where(
                Message.conversation_id == run.conversation_id,
                Message.sequence <= trigger.sequence,
            )
            .order_by(Message.sequence)
        )
        return list(self.session.scalars(query))

    def add_assistant_message(self, run_id: str, content: str) -> Message:
        run = self.get_run_by_id(run_id)
        if run is None:
            raise KeyError(run_id)
        return self.add(
            Message(
                conversation_id=run.conversation_id,
                sequence=self.next_message_sequence(run.conversation_id),
                role="assistant",
                content=content,
            )
        )

    def append_event(
        self, run_id: str, event_type: str, payload: dict
    ) -> RunEvent:
        return self.add(
            RunEvent(
                run_id=run_id,
                sequence=self.next_event_sequence(run_id),
                event_type=event_type,
                payload=payload,
            )
        )
    def add_tool_invocation(self, invocation: ToolInvocation) -> ToolInvocation:
        return self.add(invocation)

    def get_tool_invocation(self, run_id: str, tool_call_id: str) -> ToolInvocation | None:
        return self.session.scalar(
            select(ToolInvocation).where(
                ToolInvocation.run_id == run_id,
                ToolInvocation.tool_call_id == tool_call_id,
            )
        )

    def list_tool_invocations(self, run_id: str) -> list[ToolInvocation]:
        query = (
            select(ToolInvocation)
            .where(ToolInvocation.run_id == run_id)
            .order_by(ToolInvocation.created_at, ToolInvocation.id)
        )
        return list(self.session.scalars(query))

    def list_messages(
        self, project_id: str, owner_id: str, conversation_id: str
    ) -> list[Message]:
        if self.get_conversation(project_id, owner_id, conversation_id) is None:
            return []
        query = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.sequence)
        )
        return list(self.session.scalars(query))

    def next_message_sequence(self, conversation_id: str) -> int:
        conversation = self.session.scalar(
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .with_for_update()
        )
        if conversation is None:
            raise KeyError(conversation_id)
        query = select(func.coalesce(func.max(Message.sequence), 0)).where(
            Message.conversation_id == conversation_id
        )
        return int(self.session.scalar(query)) + 1
    def next_event_sequence(self, run_id: str) -> int:
        query = select(func.coalesce(func.max(RunEvent.sequence), 0)).where(
            RunEvent.run_id == run_id
        )
        return int(self.session.scalar(query)) + 1

    def list_events(self, run_id: str, after_sequence: int) -> list[RunEvent]:
        query = (
            select(RunEvent)
            .where(
                RunEvent.run_id == run_id,
                RunEvent.sequence > after_sequence,
            )
            .order_by(RunEvent.sequence)
        )
        return list(self.session.scalars(query))
