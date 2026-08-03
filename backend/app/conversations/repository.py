from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeVar

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from .models import AgentRun, Conversation, Message, RunEvent, ToolInvocation

ModelT = TypeVar("ModelT", Conversation, Message, AgentRun, RunEvent, ToolInvocation)


@dataclass(frozen=True)
class RunListResult:
    items: list[dict[str, Any]]
    total: int
    summary: dict[str, int]


class ConversationRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_runs(
        self,
        *,
        project_id: str,
        owner_id: str,
        page: int,
        page_size: int,
        status: str | None = None,
        actor_id: str | None = None,
        query: str | None = None,
        started_after: datetime | None = None,
        started_before: datetime | None = None,
    ) -> RunListResult:
        filters = [
            Conversation.project_id == project_id,
            Conversation.owner_id == owner_id,
        ]
        if status is not None:
            filters.append(AgentRun.status == status)
        if actor_id is not None:
            filters.append(AgentRun.actor_id == actor_id)
        if query:
            pattern = f"%{query}%"
            filters.append(
                or_(AgentRun.id.ilike(pattern), Conversation.title.ilike(pattern))
            )
        if started_after is not None:
            filters.append(AgentRun.created_at >= started_after)
        if started_before is not None:
            filters.append(AgentRun.created_at <= started_before)

        runs = (
            select(
                AgentRun.id,
                AgentRun.conversation_id,
                Conversation.title.label("conversation_title"),
                AgentRun.trigger_message_id,
                Message.content.label("trigger_content"),
                AgentRun.actor_type,
                AgentRun.actor_id,
                AgentRun.status,
                AgentRun.created_at,
                AgentRun.updated_at,
            )
            .join(Conversation, Conversation.id == AgentRun.conversation_id)
            .join(
                Message,
                and_(
                    Message.id == AgentRun.trigger_message_id,
                    Message.conversation_id == AgentRun.conversation_id,
                ),
            )
            .where(*filters)
            .subquery()
        )
        page_runs = (
            select(runs)
            .order_by(runs.c.created_at.desc(), runs.c.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .subquery()
        )
        page_tool_counts = (
            select(
                ToolInvocation.run_id,
                func.count(ToolInvocation.id).label("tool_invocation_count"),
            )
            .where(ToolInvocation.run_id.in_(select(page_runs.c.id)))
            .group_by(ToolInvocation.run_id)
            .subquery()
        )
        rows = self.session.execute(
            select(
                page_runs,
                func.coalesce(page_tool_counts.c.tool_invocation_count, 0).label(
                    "tool_invocation_count"
                ),
            )
            .outerjoin(page_tool_counts, page_tool_counts.c.run_id == page_runs.c.id)
            .order_by(page_runs.c.created_at.desc(), page_runs.c.id.desc())
        ).mappings()

        items = []
        for row in rows:
            created_at = row["created_at"]
            updated_at = row["updated_at"]
            duration_ms = max(0, int((updated_at - created_at).total_seconds() * 1000))
            items.append(
                {
                    "id": row["id"],
                    "conversation_id": row["conversation_id"],
                    "conversation_title": row["conversation_title"],
                    "trigger_message_id": row["trigger_message_id"],
                    "trigger_summary": " ".join(row["trigger_content"].split())[:200],
                    "actor_type": row["actor_type"],
                    "actor_id": row["actor_id"],
                    "status": row["status"],
                    "tool_invocation_count": int(row["tool_invocation_count"]),
                    "duration_ms": duration_ms,
                    "created_at": created_at,
                    "updated_at": updated_at,
                }
            )

        scoped_tool_total = (
            select(func.count(ToolInvocation.id))
            .where(ToolInvocation.run_id.in_(select(runs.c.id)))
            .scalar_subquery()
        )
        aggregate = (
            self.session.execute(
                select(
                    func.count(runs.c.id).label("total"),
                    func.coalesce(
                        func.sum(case((runs.c.status == "completed", 1), else_=0)), 0
                    ).label("completed"),
                    func.coalesce(
                        func.sum(
                            case((runs.c.status.in_(("queued", "running")), 1), else_=0)
                        ),
                        0,
                    ).label("running"),
                    func.coalesce(
                        func.sum(case((runs.c.status == "failed", 1), else_=0)), 0
                    ).label("failed"),
                    func.coalesce(scoped_tool_total, 0).label("tool_invocations"),
                )
            )
            .mappings()
            .one()
        )
        summary = {
            key: int(aggregate[key])
            for key in ("total", "completed", "running", "failed", "tool_invocations")
        }
        return RunListResult(items=items, total=summary["total"], summary=summary)

    def add(self, value: ModelT) -> ModelT:
        self.session.add(value)
        self.session.flush()
        return value

    def list_conversations(self, project_id: str, owner_id: str) -> list[Conversation]:
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

    def get_run(self, project_id: str, owner_id: str, run_id: str) -> AgentRun | None:
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

    def get_run_execution_context(self, run_id: str) -> dict[str, str] | None:
        row = (
            self.session.execute(
                select(
                    AgentRun.id.label("run_id"),
                    Conversation.id.label("conversation_id"),
                    Conversation.project_id,
                    Conversation.owner_id.label("user_id"),
                )
                .join(Conversation, Conversation.id == AgentRun.conversation_id)
                .where(AgentRun.id == run_id)
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row is not None else None

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

    def append_event(self, run_id: str, event_type: str, payload: dict) -> RunEvent:
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

    def get_tool_invocation(
        self, run_id: str, tool_call_id: str
    ) -> ToolInvocation | None:
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
        run = self.session.scalar(
            select(AgentRun).where(AgentRun.id == run_id).with_for_update()
        )
        if run is None:
            raise KeyError(run_id)
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
