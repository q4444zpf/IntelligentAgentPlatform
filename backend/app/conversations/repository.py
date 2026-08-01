from typing import TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import AgentRun, Conversation, Message, RunEvent

ModelT = TypeVar("ModelT", Conversation, Message, AgentRun, RunEvent)


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
        query = (
            select(Message)
            .where(Message.conversation_id == run.conversation_id)
            .order_by(Message.created_at, Message.id)
        )
        return list(self.session.scalars(query))

    def add_assistant_message(self, run_id: str, content: str) -> Message:
        run = self.get_run_by_id(run_id)
        if run is None:
            raise KeyError(run_id)
        return self.add(
            Message(
                conversation_id=run.conversation_id,
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
    def list_messages(
        self, project_id: str, owner_id: str, conversation_id: str
    ) -> list[Message]:
        if self.get_conversation(project_id, owner_id, conversation_id) is None:
            return []
        query = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at, Message.id)
        )
        return list(self.session.scalars(query))

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
