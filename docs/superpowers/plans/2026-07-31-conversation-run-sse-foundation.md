# Conversation, Run, and SSE Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Web chat prototype's browser-only conversation state with project-and-owner-scoped PostgreSQL conversations, messages, agent runs, and resumable SSE run events.

**Architecture:** Add a new SQLAlchemy/Alembic persistence layer for the conversation and run bounded context without migrating the existing SQLite-backed configuration modules in the same change. FastAPI creates conversations, messages, runs, and append-only events; the Vue client consumes persisted state and resumable SSE. A dispatcher interface stops this phase from pretending that an Agent is already running: production runs remain `queued` until the sandbox execution plan is implemented, while tests inject a deterministic dispatcher.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, PostgreSQL 16, psycopg 3, Pytest, Vue 3, TypeScript, Pinia, native EventSource-compatible SSE parsing.

---

## Scope Boundary

This plan implements one testable subsystem from the approved architecture specification and the approved `docs/superpowers/specs/2026-07-31-first-prototype-ui-freeze-design.md`. It does not implement Deep Agents, LangGraph, Sandbox Executor, Tool Registry, Milvus, knowledge retrieval, multi-agent execution, Artifact renderers, GIS, or the expanded five-section Agent editor. Those require separate implementation plans after this foundation is merged.

The first UI slice uses the existing Agent API as the only server-backed execution-subject directory. Team mode remains visible but disabled until the published-team directory and LangGraph execution plan are implemented. Knowledge-base and business-resource selectors remain in their frozen layout positions but are disabled with explicit unavailable text until project-scoped option APIs exist. Report, chart, file, and GIS actions are not rendered until a persisted Artifact exists. No fixed prototype option or result is submitted as production data.

The implementation order after this plan is:

1. Tool Registry and Tool Gateway.
2. Workflow Runner and Action Sandbox.
3. Deep Agent single-agent execution.
4. MCP execution and Milvus knowledge retrieval.
5. LangGraph teams and visual workflow DSL.
6. Artifact/GIS communication and lazy renderers.

## File Map

Create focused backend infrastructure and domain files:

- `backend/app/core/settings.py`: environment-backed application settings.
- `backend/app/core/database.py`: SQLAlchemy engine, session factory, and FastAPI dependency.
- `backend/app/core/request_context.py`: replaceable current-user/current-project dependency.
- `backend/app/db/base.py`: declarative model base and imported metadata.
- `backend/app/conversations/models.py`: Conversation, Message, AgentRun, and RunEvent tables.
- `backend/app/conversations/schemas.py`: API request and response contracts.
- `backend/app/conversations/repository.py`: project-and-owner-scoped persistence queries.
- `backend/app/conversations/service.py`: transaction and state-transition rules.
- `backend/app/conversations/dispatcher.py`: execution-dispatch boundary.
- `backend/app/conversations/router.py`: conversation, run, and SSE routes.
- `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/versions/20260731_01_conversation_run_foundation.py`: migrations.
- `backend/tests/conversations/`: unit and API tests for the new bounded context.

Create focused frontend files:

- `frontend/src/api/conversations.ts`: conversation/run API contracts.
- `frontend/src/api/runEvents.ts`: resumable SSE client.
- `frontend/src/features/chat/runtimeStatus.ts`: authoritative Run status labels for the chat UI.
- `frontend/src/stores/conversations.ts`: server-backed chat state.
- `frontend/src/views/agent/AgentConsoleView.vue`: remove fixed sessions and simulated reply.

Modify deployment and documentation files:

- `backend/requirements.txt`: SQLAlchemy, Alembic, and psycopg.
- `backend/app/main.py`: lifespan and router registration.
- `compose.yaml`: PostgreSQL service and API database configuration.
- `README.md` and `backend/README.md`: migration and development commands.

### Task 1: Add PostgreSQL and SQLAlchemy Infrastructure

**Files:**
- Create: `backend/app/core/__init__.py`
- Create: `backend/app/core/settings.py`
- Create: `backend/app/core/database.py`
- Create: `backend/app/db/__init__.py`
- Create: `backend/app/db/base.py`
- Modify: `backend/requirements.txt`
- Test: `backend/tests/core/test_settings.py`
- Test: `backend/tests/core/test_database.py`

- [ ] **Step 1: Write failing settings tests**

```python
# backend/tests/core/test_settings.py
from app.core.settings import Settings


def test_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings.from_env()
    assert settings.database_url == "postgresql+psycopg://iap:iap@127.0.0.1:5432/iap"


def test_reads_database_and_dev_identity_settings(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db/app")
    monkeypatch.setenv("IAP_ALLOW_DEV_IDENTITY", "true")
    settings = Settings.from_env()
    assert settings.database_url.endswith("@db/app")
    assert settings.allow_dev_identity is True
```

- [ ] **Step 2: Run the tests and verify the import fails**

Run: `cd backend; python -m pytest tests/core/test_settings.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.core'`.

- [ ] **Step 3: Add dependencies and settings**

Append to `backend/requirements.txt`:

```text
sqlalchemy>=2.0.43,<3
alembic>=1.16.5,<2
psycopg[binary]>=3.2.10,<4
```

Create `backend/app/core/settings.py`:

```python
from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    database_url: str
    allow_dev_identity: bool

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv(
                "DATABASE_URL",
                "postgresql+psycopg://iap:iap@127.0.0.1:5432/iap",
            ),
            allow_dev_identity=os.getenv("IAP_ALLOW_DEV_IDENTITY", "false").lower()
            in {"1", "true", "yes"},
        )


settings = Settings.from_env()
```

- [ ] **Step 4: Write the failing database session test**

```python
# backend/tests/core/test_database.py
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import create_session_factory


def test_session_factory_executes_sqlite_for_unit_tests():
    factory = create_session_factory("sqlite+pysqlite:///:memory:")
    with factory() as session:
        assert session.scalar(text("select 1")) == 1
        assert isinstance(session, Session)
```

- [ ] **Step 5: Implement the database boundary**

```python
# backend/app/core/database.py
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .settings import settings


def create_session_factory(database_url: str) -> sessionmaker[Session]:
    engine = create_engine(database_url, pool_pre_ping=True)
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


SessionFactory = create_session_factory(settings.database_url)


def get_session() -> Generator[Session, None, None]:
    with SessionFactory() as session:
        yield session
```

```python
# backend/app/db/base.py
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

Create empty `__init__.py` files in `backend/app/core/` and `backend/app/db/`.

- [ ] **Step 6: Run focused tests**

Run: `cd backend; python -m pytest tests/core -v`

Expected: 3 PASS.

- [ ] **Step 7: Commit the infrastructure**

```powershell
git add backend/app/core backend/app/db backend/tests/core backend/requirements.txt
git commit -m "feat: add shared database infrastructure"
```

### Task 2: Add Request Identity and Project Scope

**Files:**
- Create: `backend/app/core/request_context.py`
- Test: `backend/tests/core/test_request_context.py`

- [ ] **Step 1: Write failing dependency tests**

```python
# backend/tests/core/test_request_context.py
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.request_context import RequestContext, require_request_context


def build_client(allow_dev_identity: bool) -> TestClient:
    app = FastAPI()

    @app.get("/context")
    def context(value: RequestContext = Depends(require_request_context)):
        return value

    app.state.allow_dev_identity = allow_dev_identity
    return TestClient(app)


def test_rejects_missing_identity():
    assert build_client(True).get("/context").status_code == 401


def test_rejects_headers_when_dev_identity_is_disabled():
    response = build_client(False).get(
        "/context",
        headers={"X-User-ID": "user-1", "X-Project-ID": "project-1"},
    )
    assert response.status_code == 401


def test_accepts_explicit_dev_identity():
    response = build_client(True).get(
        "/context",
        headers={"X-User-ID": "user-1", "X-Project-ID": "project-1"},
    )
    assert response.json() == {"user_id": "user-1", "project_id": "project-1"}
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `cd backend; python -m pytest tests/core/test_request_context.py -v`

Expected: FAIL because `request_context.py` does not exist.

- [ ] **Step 3: Implement the replaceable request context**

```python
# backend/app/core/request_context.py
from typing import Annotated

from fastapi import Header, HTTPException, Request
from pydantic import BaseModel


class RequestContext(BaseModel):
    user_id: str
    project_id: str


def require_request_context(
    request: Request,
    user_id: Annotated[str | None, Header(alias="X-User-ID")] = None,
    project_id: Annotated[str | None, Header(alias="X-Project-ID")] = None,
) -> RequestContext:
    if not getattr(request.app.state, "allow_dev_identity", False):
        raise HTTPException(status_code=401, detail="Authentication is required")
    if not user_id or not project_id:
        raise HTTPException(status_code=401, detail="User and project headers are required")
    return RequestContext(user_id=user_id, project_id=project_id)
```

This is an explicitly development-only adapter. A later identity plan replaces this dependency with authenticated server sessions; production must keep `IAP_ALLOW_DEV_IDENTITY=false`.

- [ ] **Step 4: Run focused tests**

Run: `cd backend; python -m pytest tests/core/test_request_context.py -v`

Expected: 3 PASS.

- [ ] **Step 5: Commit request scoping**

```powershell
git add backend/app/core/request_context.py backend/tests/core/test_request_context.py
git commit -m "feat: add project request context boundary"
```

### Task 3: Model Conversations, Messages, Runs, and Events

**Files:**
- Create: `backend/app/conversations/__init__.py`
- Create: `backend/app/conversations/models.py`
- Create: `backend/app/conversations/schemas.py`
- Modify: `backend/app/db/base.py`
- Test: `backend/tests/conversations/test_models.py`

- [ ] **Step 1: Write a failing persistence test**

```python
# backend/tests/conversations/test_models.py
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.conversations.models import AgentRun, Conversation, Message, RunEvent
from app.db.base import Base


def test_persists_project_scoped_conversation_graph():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        conversation = Conversation(project_id="p1", owner_id="u1", title="洪水研判")
        session.add(conversation)
        session.flush()
        message = Message(conversation_id=conversation.id, role="user", content="分析洪峰")
        session.add(message)
        session.flush()
        run = AgentRun(
            conversation_id=conversation.id,
            trigger_message_id=message.id,
            actor_type="agent",
            actor_id="flood",
            status="queued",
        )
        session.add(run)
        session.flush()
        session.add(RunEvent(run_id=run.id, sequence=1, event_type="run.status", payload={"status": "queued"}))
        session.commit()
        assert session.scalar(select(Conversation)).project_id == "p1"
        assert session.scalar(select(RunEvent)).sequence == 1
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `cd backend; python -m pytest tests/conversations/test_models.py -v`

Expected: FAIL because `app.conversations.models` does not exist.

- [ ] **Step 3: Implement focused SQLAlchemy models**

```python
# backend/app/conversations/models.py
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def new_id() -> str:
    return str(uuid.uuid4())


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(200))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    trigger_message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"))
    actor_type: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_run_event_sequence"),
        Index("ix_run_events_resume", "run_id", "sequence"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 4: Add complete Pydantic contracts**

```python
# backend/app/conversations/schemas.py
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ConversationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ConversationInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_id: str
    owner_id: str
    title: str
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=50_000)
    actor_type: Literal["agent", "team"]
    actor_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")


class MessageInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    conversation_id: str
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    created_at: datetime


class AgentRunInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    conversation_id: str
    trigger_message_id: str
    actor_type: Literal["agent", "team"]
    actor_id: str
    status: str
    created_at: datetime
    updated_at: datetime


class MessageAccepted(BaseModel):
    message: MessageInfo
    run: AgentRunInfo


class RunEventInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    run_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime
```

Import `Conversation`, `Message`, `AgentRun`, and `RunEvent` at the bottom of `backend/app/db/base.py` so Alembic sees their metadata.

- [ ] **Step 5: Run the model tests**

Run: `cd backend; python -m pytest tests/conversations/test_models.py -v`

Expected: 1 PASS.

- [ ] **Step 6: Commit the domain model**

```powershell
git add backend/app/conversations backend/app/db/base.py backend/tests/conversations/test_models.py
git commit -m "feat: model conversations and agent runs"
```

### Task 4: Implement Project-and-Owner-Scoped Services and Dispatcher Boundary

**Files:**
- Create: `backend/app/conversations/repository.py`
- Create: `backend/app/conversations/dispatcher.py`
- Create: `backend/app/conversations/service.py`
- Test: `backend/tests/conversations/test_service.py`

- [ ] **Step 1: Write failing service behavior tests**

```python
# backend/tests/conversations/test_service.py
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.conversations.dispatcher import RunDispatcher
from app.conversations.repository import ConversationRepository
from app.conversations.schemas import ConversationCreate, MessageCreate
from app.conversations.service import ConversationNotFound, ConversationService
from app.core.request_context import RequestContext
from app.db.base import Base


class RecordingDispatcher(RunDispatcher):
    def __init__(self):
        self.run_ids: list[str] = []

    def dispatch(self, run_id: str) -> None:
        self.run_ids.append(run_id)


def build_service():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    dispatcher = RecordingDispatcher()
    return session, dispatcher, ConversationService(ConversationRepository(session), dispatcher)


def test_creates_message_run_and_initial_event_atomically():
    session, dispatcher, service = build_service()
    context = RequestContext(user_id="u1", project_id="p1")
    conversation = service.create_conversation(context, ConversationCreate(title="洪水研判"))
    accepted = service.create_message(
        context,
        conversation.id,
        MessageCreate(content="分析未来洪峰", actor_type="agent", actor_id="flood"),
    )
    assert accepted.run.status == "queued"
    assert service.list_events(context, accepted.run.id, after_sequence=0)[0].payload == {"status": "queued"}
    assert dispatcher.run_ids == [accepted.run.id]
    session.close()


def test_cannot_read_another_project_conversation():
    _, _, service = build_service()
    owner = RequestContext(user_id="u1", project_id="p1")
    conversation = service.create_conversation(owner, ConversationCreate(title="项目一"))
    other = RequestContext(user_id="u2", project_id="p2")
    try:
        service.get_conversation(other, conversation.id)
    except ConversationNotFound:
        pass
    else:
        raise AssertionError("cross-project access must look like not found")


def test_cannot_read_another_users_private_conversation_in_same_project():
    _, _, service = build_service()
    owner = RequestContext(user_id="u1", project_id="p1")
    conversation = service.create_conversation(owner, ConversationCreate(title="个人研判"))
    other_user = RequestContext(user_id="u2", project_id="p1")
    try:
        service.get_conversation(other_user, conversation.id)
    except ConversationNotFound:
        pass
    else:
        raise AssertionError("cross-owner access must look like not found")
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `cd backend; python -m pytest tests/conversations/test_service.py -v`

Expected: FAIL because repository, dispatcher, and service do not exist.

- [ ] **Step 3: Implement the dispatcher boundary**

```python
# backend/app/conversations/dispatcher.py
from abc import ABC, abstractmethod


class RunDispatcher(ABC):
    @abstractmethod
    def dispatch(self, run_id: str) -> None:
        raise NotImplementedError


class UnavailableRunDispatcher(RunDispatcher):
    def dispatch(self, run_id: str) -> None:
        # The run stays queued until the sandbox execution subsystem is installed.
        return None
```

- [ ] **Step 4: Implement project-scoped repository methods**

```python
# backend/app/conversations/repository.py
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import AgentRun, Conversation, Message, RunEvent


class ConversationRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, value):
        self.session.add(value)
        self.session.flush()
        return value

    def list_conversations(self, project_id: str, owner_id: str) -> list[Conversation]:
        query = select(Conversation).where(
            Conversation.project_id == project_id,
            Conversation.owner_id == owner_id,
            Conversation.archived_at.is_(None),
        ).order_by(Conversation.updated_at.desc())
        return list(self.session.scalars(query))

    def get_conversation(self, project_id: str, owner_id: str, conversation_id: str) -> Conversation | None:
        return self.session.scalar(select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.project_id == project_id,
            Conversation.owner_id == owner_id,
        ))

    def get_run(self, project_id: str, owner_id: str, run_id: str) -> AgentRun | None:
        return self.session.scalar(
            select(AgentRun).join(Conversation).where(
                AgentRun.id == run_id,
                Conversation.project_id == project_id,
                Conversation.owner_id == owner_id,
            )
        )

    def list_messages(self, project_id: str, owner_id: str, conversation_id: str) -> list[Message]:
        if self.get_conversation(project_id, owner_id, conversation_id) is None:
            return []
        return list(self.session.scalars(select(Message).where(
            Message.conversation_id == conversation_id,
        ).order_by(Message.created_at, Message.id)))

    def next_event_sequence(self, run_id: str) -> int:
        return int(self.session.scalar(select(func.coalesce(func.max(RunEvent.sequence), 0)).where(RunEvent.run_id == run_id))) + 1

    def list_events(self, run_id: str, after_sequence: int) -> list[RunEvent]:
        return list(self.session.scalars(select(RunEvent).where(
            RunEvent.run_id == run_id,
            RunEvent.sequence > after_sequence,
        ).order_by(RunEvent.sequence)))
```

- [ ] **Step 5: Implement transactional service methods**

Implement `ConversationService` completely:

```python
from .dispatcher import RunDispatcher
from .models import AgentRun, Conversation, Message, RunEvent
from .repository import ConversationRepository
from .schemas import (
    AgentRunInfo, ConversationCreate, ConversationInfo, MessageAccepted,
    MessageCreate, MessageInfo, RunEventInfo,
)
from app.core.request_context import RequestContext


class ConversationNotFound(Exception):
    pass


class RunNotFound(Exception):
    pass


class ConversationService:
    def __init__(self, repository: ConversationRepository, dispatcher: RunDispatcher):
        self.repository = repository
        self.dispatcher = dispatcher

    def create_conversation(self, context: RequestContext, request: ConversationCreate) -> ConversationInfo:
        value = self.repository.add(Conversation(
            project_id=context.project_id,
            owner_id=context.user_id,
            title=request.title,
        ))
        self.repository.session.commit()
        return ConversationInfo.model_validate(value)

    def list_conversations(self, context: RequestContext) -> list[ConversationInfo]:
        return [ConversationInfo.model_validate(value) for value in self.repository.list_conversations(
            context.project_id, context.user_id,
        )]

    def get_conversation(self, context: RequestContext, conversation_id: str) -> ConversationInfo:
        value = self.repository.get_conversation(context.project_id, context.user_id, conversation_id)
        if value is None:
            raise ConversationNotFound(conversation_id)
        return ConversationInfo.model_validate(value)

    def list_messages(self, context: RequestContext, conversation_id: str) -> list[MessageInfo]:
        if self.repository.get_conversation(context.project_id, context.user_id, conversation_id) is None:
            raise ConversationNotFound(conversation_id)
        return [MessageInfo.model_validate(value) for value in self.repository.list_messages(
            context.project_id, context.user_id, conversation_id,
        )]

    def create_message(
        self,
        context: RequestContext,
        conversation_id: str,
        request: MessageCreate,
    ) -> MessageAccepted:
        if self.repository.get_conversation(context.project_id, context.user_id, conversation_id) is None:
            raise ConversationNotFound(conversation_id)
        message = self.repository.add(Message(
            conversation_id=conversation_id,
            role="user",
            content=request.content,
        ))
        run = self.repository.add(AgentRun(
            conversation_id=conversation_id,
            trigger_message_id=message.id,
            actor_type=request.actor_type,
            actor_id=request.actor_id,
            status="queued",
        ))
        self.repository.add(RunEvent(
            run_id=run.id,
            sequence=1,
            event_type="run.status",
            payload={"status": "queued"},
        ))
        self.repository.session.commit()
        self.dispatcher.dispatch(run.id)
        return MessageAccepted(
            message=MessageInfo.model_validate(message),
            run=AgentRunInfo.model_validate(run),
        )

    def get_run(self, context: RequestContext, run_id: str) -> AgentRunInfo:
        value = self.repository.get_run(context.project_id, context.user_id, run_id)
        if value is None:
            raise RunNotFound(run_id)
        return AgentRunInfo.model_validate(value)

    def list_events(
        self,
        context: RequestContext,
        run_id: str,
        after_sequence: int,
    ) -> list[RunEventInfo]:
        if self.repository.get_run(context.project_id, context.user_id, run_id) is None:
            raise RunNotFound(run_id)
        return [RunEventInfo.model_validate(value) for value in self.repository.list_events(
            run_id, after_sequence,
        )]
```

`create_message` adds Message, AgentRun, and initial `run.status` event in one transaction, commits before dispatch, then calls `dispatcher.dispatch(run.id)`. Missing, cross-project, and cross-owner records use the same exceptions so the API never reveals that another project or user owns an ID. Shared team conversations require a future explicit membership/ACL model; this foundation does not weaken private-conversation isolation to simulate sharing.

- [ ] **Step 6: Run service tests**

Run: `cd backend; python -m pytest tests/conversations/test_service.py -v`

Expected: 3 PASS.

- [ ] **Step 7: Commit the service boundary**

```powershell
git add backend/app/conversations backend/tests/conversations/test_service.py
git commit -m "feat: add project scoped conversation service"
```

### Task 5: Add REST and Resumable SSE APIs

**Files:**
- Create: `backend/app/conversations/router.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/conversations/test_api.py`

- [ ] **Step 1: Write failing API tests**

```python
# backend/tests/conversations/test_api.py
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.conversations.dispatcher import UnavailableRunDispatcher
from app.conversations.repository import ConversationRepository
from app.conversations.router import create_router
from app.conversations.service import ConversationService
from app.core.database import get_session
from app.db.base import Base


def build_client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine)
    service = ConversationService(ConversationRepository(session), UnavailableRunDispatcher())
    app = FastAPI()
    app.state.allow_dev_identity = True
    app.dependency_overrides[get_session] = lambda: session
    app.include_router(create_router(lambda _session: service), prefix="/api")
    return TestClient(app)


HEADERS = {"X-User-ID": "u1", "X-Project-ID": "p1"}


def test_message_creation_returns_202_and_run():
    client = build_client()
    conversation = client.post("/api/conversations", json={"title": "洪水研判"}, headers=HEADERS).json()
    response = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "分析洪峰", "actor_type": "agent", "actor_id": "flood"},
        headers=HEADERS,
    )
    assert response.status_code == 202
    assert response.json()["run"]["status"] == "queued"


def test_sse_honors_last_event_id():
    client = build_client()
    conversation = client.post("/api/conversations", json={"title": "洪水研判"}, headers=HEADERS).json()
    accepted = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "分析洪峰", "actor_type": "agent", "actor_id": "flood"},
        headers=HEADERS,
    ).json()
    response = client.get(
        f"/api/agent-runs/{accepted['run']['id']}/events",
        headers=HEADERS | {"Last-Event-ID": "0"},
    )
    assert "id: 1" in response.text
    assert "event: run.status" in response.text
    assert 'data: {"status":"queued"}' in response.text
```

- [ ] **Step 2: Run tests and verify router import fails**

Run: `cd backend; python -m pytest tests/conversations/test_api.py -v`

Expected: FAIL because `app.conversations.router` does not exist.

- [ ] **Step 3: Implement REST routes and exception mapping**

Create routes for:

```text
POST /conversations
GET  /conversations
GET  /conversations/{conversation_id}
GET  /conversations/{conversation_id}/messages
POST /conversations/{conversation_id}/messages
GET  /agent-runs/{run_id}
GET  /agent-runs/{run_id}/events
```

Use `Depends(require_request_context)` on every route. Return `202 Accepted` from message creation. Map both missing and cross-project records to HTTP 404.

Implement `backend/app/conversations/router.py`:

```python
import json
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.request_context import RequestContext, require_request_context

from .dispatcher import UnavailableRunDispatcher
from .repository import ConversationRepository
from .schemas import (
    AgentRunInfo, ConversationCreate, ConversationInfo, MessageAccepted,
    MessageCreate, MessageInfo, RunEventInfo,
)
from .service import ConversationNotFound, ConversationService, RunNotFound


ServiceFactory = Callable[[Session], ConversationService]


def default_service_factory(session: Session) -> ConversationService:
    return ConversationService(ConversationRepository(session), UnavailableRunDispatcher())


def encode_sse(event: RunEventInfo) -> str:
    data = json.dumps(event.payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.sequence}\nevent: {event.event_type}\ndata: {data}\n\n"


def create_router(service_factory: ServiceFactory = default_service_factory) -> APIRouter:
    router = APIRouter()

    def service(session: Session = Depends(get_session)) -> ConversationService:
        return service_factory(session)

    def not_found(operation):
        try:
            return operation()
        except (ConversationNotFound, RunNotFound) as error:
            raise HTTPException(status_code=404, detail="Resource was not found") from error

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

    @router.get("/conversations/{conversation_id}/messages", response_model=list[MessageInfo])
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
        return not_found(lambda: manager.create_message(context, conversation_id, request))

    @router.get("/agent-runs/{run_id}", response_model=AgentRunInfo)
    def get_run(
        run_id: str,
        context: RequestContext = Depends(require_request_context),
        manager: ConversationService = Depends(service),
    ):
        return not_found(lambda: manager.get_run(context, run_id))

    @router.get("/agent-runs/{run_id}/events")
    def get_events(
        run_id: str,
        last_event_id: Annotated[int, Header(alias="Last-Event-ID")] = 0,
        context: RequestContext = Depends(require_request_context),
        manager: ConversationService = Depends(service),
    ):
        events = not_found(lambda: manager.list_events(context, run_id, last_event_id))
        return StreamingResponse(
            iter(encode_sse(event) for event in events),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    return router


router = create_router()
```

- [ ] **Step 4: Implement finite resumable SSE for the foundation**

The initial endpoint returns persisted events after `Last-Event-ID` and then closes. `encode_sse` above formats each event exactly as:

```python
id: 1
event: run.status
data: {"status":"queued"}
```

Return `StreamingResponse(iter(encoded_events), media_type="text/event-stream")` with `Cache-Control: no-cache`. The long-lived PostgreSQL/Redis event tail is added with the sandbox runner plan; this phase establishes replay semantics without polling the database forever inside an API process.

- [ ] **Step 5: Register settings and router in the app**

In `backend/app/main.py`, set:

```python
app.state.allow_dev_identity = settings.allow_dev_identity
app.include_router(conversations_router, prefix="/api", tags=["conversations"])
```

Construct the route service from the request-scoped SQLAlchemy session and `UnavailableRunDispatcher`.

- [ ] **Step 6: Run API and existing backend tests**

Run: `cd backend; python -m pytest tests/conversations/test_api.py -v`

Expected: 2 PASS.

Run: `cd backend; python -m pytest -q`

Expected: all existing and new tests PASS.

- [ ] **Step 7: Commit the API**

```powershell
git add backend/app/conversations/router.py backend/app/main.py backend/tests/conversations/test_api.py
git commit -m "feat: add conversation run and sse api"
```

### Task 6: Add Alembic Migration and PostgreSQL Service

**Files:**
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/script.py.mako`
- Create: `backend/alembic/versions/20260731_01_conversation_run_foundation.py`
- Modify: `compose.yaml`
- Modify: `backend/Dockerfile`
- Test: `backend/tests/integration/test_postgres_migrations.py`

- [ ] **Step 1: Initialize Alembic and replace generated metadata wiring**

Run: `cd backend; python -m alembic init alembic`

In `alembic/env.py`, set `target_metadata = Base.metadata` and override the URL from `DATABASE_URL`:

```python
from app.core.settings import settings
from app.db.base import Base

config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = Base.metadata
```

- [ ] **Step 2: Create the explicit initial migration**

Run: `cd backend; python -m alembic revision --autogenerate -m "conversation run foundation"`

Inspect the generated migration. It must create `conversations`, `messages`, `agent_runs`, and `run_events`, including `uq_run_event_sequence` and `ix_run_events_resume`. It must not contain existing SQLite configuration tables because those remain outside SQLAlchemy in this phase.

- [ ] **Step 3: Add PostgreSQL to Compose**

Add:

```yaml
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: iap
      POSTGRES_USER: iap
      POSTGRES_PASSWORD: iap
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U iap -d iap"]
      interval: 5s
      timeout: 3s
      retries: 10
    volumes:
      - postgres-data:/var/lib/postgresql/data

  api:
    environment:
      DATABASE_URL: postgresql+psycopg://iap:iap@postgres:5432/iap
      IAP_ALLOW_DEV_IDENTITY: "true"
    depends_on:
      postgres:
        condition: service_healthy
```

Add `postgres-data:` to top-level volumes. Update `backend/Dockerfile` so the image includes Alembic files and applies migrations before Uvicorn:

```dockerfile
COPY backend/app ./app
COPY backend/alembic.ini ./alembic.ini
COPY backend/alembic ./alembic
RUN mkdir -p /data && chown -R nobody:nogroup /data /app

USER nobody

EXPOSE 8000

CMD ["sh", "-c", "python -m alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
```

Do not call `Base.metadata.create_all()` in production startup.

- [ ] **Step 4: Add a migration smoke test**

```python
# backend/tests/integration/test_postgres_migrations.py
import os
import subprocess

import pytest
from sqlalchemy import create_engine, inspect


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")
def test_upgrade_head_creates_conversation_tables():
    env = os.environ | {"DATABASE_URL": os.environ["TEST_DATABASE_URL"]}
    subprocess.run(["python", "-m", "alembic", "upgrade", "head"], check=True, env=env)
    tables = set(inspect(create_engine(env["DATABASE_URL"])).get_table_names())
    assert {"conversations", "messages", "agent_runs", "run_events"} <= tables
```

- [ ] **Step 5: Verify migration against Compose PostgreSQL**

Run: `docker compose up -d postgres`

Run: `$env:DATABASE_URL='postgresql+psycopg://iap:iap@127.0.0.1:5432/iap'; cd backend; python -m alembic upgrade head; python -m alembic current`

Expected: current revision is `20260731_01 (head)`.

- [ ] **Step 6: Run migration test**

Run: `$env:TEST_DATABASE_URL='postgresql+psycopg://iap:iap@127.0.0.1:5432/iap'; cd backend; python -m pytest tests/integration/test_postgres_migrations.py -v`

Expected: 1 PASS.

- [ ] **Step 7: Commit database deployment**

```powershell
git add backend/alembic.ini backend/alembic backend/Dockerfile backend/tests/integration/test_postgres_migrations.py compose.yaml
git commit -m "feat: deploy conversation storage on postgres"
```

### Task 7: Add Frontend Conversation API and SSE Client

**Files:**
- Create: `frontend/src/api/conversations.ts`
- Create: `frontend/src/api/runEvents.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/vite-env.d.ts`
- Test: `frontend/src/api/runEvents.test.ts`
- Modify: `frontend/package.json`

- [ ] **Step 1: Add Vitest and the test script**

Add `vitest` to devDependencies and scripts:

```json
"test": "vitest run"
```

Run: `cd frontend; npm install`

- [ ] **Step 2: Write the failing SSE parser test**

```typescript
// frontend/src/api/runEvents.test.ts
import { describe, expect, it } from 'vitest';
import { parseSseFrame } from './runEvents';

describe('parseSseFrame', () => {
  it('parses persisted run events', () => {
    expect(parseSseFrame('id: 4\nevent: run.status\ndata: {"status":"running"}')).toEqual({
      sequence: 4,
      event_type: 'run.status',
      payload: { status: 'running' },
    });
  });
});
```

- [ ] **Step 3: Run the test and verify it fails**

Run: `cd frontend; npm test -- src/api/runEvents.test.ts`

Expected: FAIL because `parseSseFrame` does not exist.

- [ ] **Step 4: Add conversation contracts and API methods**

```typescript
// frontend/src/api/conversations.ts
import { request } from './client';

export interface ConversationInfo {
  id: string; project_id: string; owner_id: string; title: string;
  archived_at: string | null; created_at: string; updated_at: string;
}
export interface MessageInfo {
  id: string; conversation_id: string; role: 'user' | 'assistant' | 'system' | 'tool';
  content: string; created_at: string;
}
export interface AgentRunInfo {
  id: string; conversation_id: string; trigger_message_id: string;
  actor_type: 'agent' | 'team'; actor_id: string; status: string;
  created_at: string; updated_at: string;
}
export interface MessageAccepted { message: MessageInfo; run: AgentRunInfo }

export const conversationsApi = {
  list: () => request<ConversationInfo[]>('/conversations'),
  listMessages: (conversationId: string) =>
    request<MessageInfo[]>(`/conversations/${encodeURIComponent(conversationId)}/messages`),
  create: (title: string) => request<ConversationInfo>('/conversations', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title }),
  }),
  sendMessage: (conversationId: string, body: { content: string; actor_type: 'agent' | 'team'; actor_id: string }) =>
    request<MessageAccepted>(`/conversations/${encodeURIComponent(conversationId)}/messages`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }),
};
```

- [ ] **Step 5: Add development identity headers centrally**

Export the base URL and development identity headers from `frontend/src/api/client.ts`, and include them only when both Vite variables exist:

```typescript
export const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || '/api';
export const identityHeaders: Record<string, string> = import.meta.env.VITE_DEV_USER_ID && import.meta.env.VITE_DEV_PROJECT_ID
  ? { 'X-User-ID': import.meta.env.VITE_DEV_USER_ID, 'X-Project-ID': import.meta.env.VITE_DEV_PROJECT_ID }
  : {};
```

Replace the private `baseUrl` use with `apiBaseUrl`, and merge `identityHeaders` before caller headers:

```typescript
headers: { Accept: 'application/json', ...identityHeaders, ...init.headers },
```

Do not hard-code identities in production bundles.

Declare the development variables in `frontend/src/vite-env.d.ts`:

```typescript
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_DEV_USER_ID?: string;
  readonly VITE_DEV_PROJECT_ID?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
```

- [ ] **Step 6: Implement finite SSE fetch and parser**

Because custom headers are required during the development identity phase, use `fetch`, not browser `EventSource`:

```typescript
export interface RunEvent {
  sequence: number;
  event_type: string;
  payload: Record<string, unknown>;
}

export function parseSseFrame(frame: string): RunEvent {
  const lines = Object.fromEntries(frame.split('\n').map((line) => {
    const index = line.indexOf(':');
    return [line.slice(0, index), line.slice(index + 1).trim()];
  }));
  return { sequence: Number(lines.id), event_type: lines.event, payload: JSON.parse(lines.data) };
}

export async function getRunEvents(runId: string, afterSequence: number): Promise<RunEvent[]> {
  const response = await fetch(`${apiBaseUrl}/agent-runs/${encodeURIComponent(runId)}/events`, {
    headers: {
      Accept: 'text/event-stream',
      'Last-Event-ID': String(afterSequence),
      ...identityHeaders,
    },
  });
  if (!response.ok) throw new ApiError(`运行事件读取失败（HTTP ${response.status}）`, response.status);
  const text = await response.text();
  return text.split(/\r?\n\r?\n/).filter((frame) => frame.trim()).map(parseSseFrame);
}
```

Import `ApiError`, `apiBaseUrl`, and `identityHeaders` from `./client` in `runEvents.ts`.

- [ ] **Step 7: Run frontend tests and build**

Run: `cd frontend; npm test`

Expected: all Vitest tests PASS.

Run: `cd frontend; npm run build`

Expected: `vue-tsc` and Vite build PASS.

- [ ] **Step 8: Commit frontend transport**

```powershell
git add frontend/src/api frontend/src/vite-env.d.ts frontend/package.json frontend/package-lock.json
git commit -m "feat: add conversation and run event client"
```

### Task 8: Replace Prototype Chat State With the Server Store

**Files:**
- Create: `frontend/src/stores/conversations.ts`
- Create: `frontend/src/features/chat/runtimeStatus.ts`
- Modify: `frontend/src/views/agent/AgentConsoleView.vue`
- Test: `frontend/src/stores/conversations.test.ts`
- Test: `frontend/src/features/chat/runtimeStatus.test.ts`

- [ ] **Step 1: Write a failing store test**

```typescript
// frontend/src/stores/conversations.test.ts
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { conversationsApi } from '@/api/conversations';
import { useConversationStore } from './conversations';

vi.mock('@/api/conversations', () => ({
  conversationsApi: { list: vi.fn(), create: vi.fn(), sendMessage: vi.fn() },
}));

describe('conversation store', () => {
  beforeEach(() => setActivePinia(createPinia()));

  it('keeps the accepted run queued instead of fabricating a reply', async () => {
    vi.mocked(conversationsApi.sendMessage).mockResolvedValue({
      message: { id: 'm1', conversation_id: 'c1', role: 'user', content: '分析洪峰', created_at: '2026-07-31T00:00:00Z' },
      run: { id: 'r1', conversation_id: 'c1', trigger_message_id: 'm1', actor_type: 'agent', actor_id: 'flood', status: 'queued', created_at: '2026-07-31T00:00:00Z', updated_at: '2026-07-31T00:00:00Z' },
    });
    const store = useConversationStore();
    store.activeConversationId = 'c1';
    await store.sendMessage('分析洪峰', 'agent', 'flood');
    expect(store.activeRun?.status).toBe('queued');
    expect(store.messages).toHaveLength(1);
  });
});
```

Add a focused status-label test:

```typescript
// frontend/src/features/chat/runtimeStatus.test.ts
import { describe, expect, it } from 'vitest';
import { runtimeStatusLabel } from './runtimeStatus';

describe('runtimeStatusLabel', () => {
  it('does not claim isolation while a run is only queued', () => {
    expect(runtimeStatusLabel('queued')).toBe('等待沙箱执行服务');
  });

  it('maps all frozen run states to explicit labels', () => {
    expect(runtimeStatusLabel('starting')).toBe('正在创建隔离运行环境');
    expect(runtimeStatusLabel('running')).toBe('沙箱运行中');
    expect(runtimeStatusLabel('waiting_approval')).toBe('等待人工确认');
    expect(runtimeStatusLabel('succeeded')).toBe('运行完成');
    expect(runtimeStatusLabel('failed')).toBe('运行失败');
    expect(runtimeStatusLabel('cancelled')).toBe('已取消');
  });
});
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `cd frontend; npm test -- src/stores/conversations.test.ts`

Expected: FAIL because the store and `runtimeStatusLabel` do not exist.

- [ ] **Step 3: Implement the Pinia store**

Implement the store completely:

```typescript
import { defineStore } from 'pinia';
import {
  conversationsApi,
  type AgentRunInfo,
  type ConversationInfo,
  type MessageInfo,
} from '@/api/conversations';
import { getRunEvents, type RunEvent } from '@/api/runEvents';

export const useConversationStore = defineStore('conversations', {
  state: () => ({
    conversations: [] as ConversationInfo[],
    activeConversationId: '',
    messages: [] as MessageInfo[],
    activeRun: null as AgentRunInfo | null,
    events: [] as RunEvent[],
    loading: false,
    sending: false,
    error: '',
  }),
  actions: {
    async loadConversations() {
      this.loading = true;
      this.error = '';
      try {
        this.conversations = await conversationsApi.list();
        if (!this.activeConversationId && this.conversations.length) {
          await this.selectConversation(this.conversations[0].id);
        }
      } catch (error) {
        this.error = error instanceof Error ? error.message : '会话加载失败';
      } finally {
        this.loading = false;
      }
    },
    async createConversation(title: string) {
      const conversation = await conversationsApi.create(title);
      this.conversations.unshift(conversation);
      this.activeConversationId = conversation.id;
      this.messages = [];
      this.activeRun = null;
      this.events = [];
      return conversation;
    },
    async selectConversation(conversationId: string) {
      this.activeConversationId = conversationId;
      this.messages = await conversationsApi.listMessages(conversationId);
      this.activeRun = null;
      this.events = [];
    },
    startNewConversation() {
      this.activeConversationId = '';
      this.messages = [];
      this.activeRun = null;
      this.events = [];
    },
    async sendMessage(content: string, actorType: 'agent' | 'team', actorId: string) {
      this.sending = true;
      try {
        if (!this.activeConversationId) await this.createConversation(content.slice(0, 40));
        const accepted = await conversationsApi.sendMessage(this.activeConversationId, {
          content,
          actor_type: actorType,
          actor_id: actorId,
        });
        this.messages.push(accepted.message);
        this.activeRun = accepted.run;
        await this.replayEvents();
      } finally {
        this.sending = false;
      }
    },
    async replayEvents() {
      if (!this.activeRun) return;
      const latest = this.events.at(-1)?.sequence ?? 0;
      const events = await getRunEvents(this.activeRun.id, latest);
      this.events.push(...events);
      for (const event of events) {
        if (event.event_type === 'run.status' && typeof event.payload.status === 'string') {
          this.activeRun.status = event.payload.status;
        }
      }
    },
  },
});
```

Do not add an assistant message until a future event contains persisted assistant content. Never use a timer to fabricate a result.

- [ ] **Step 4: Implement the frozen Run status mapping**

```typescript
// frontend/src/features/chat/runtimeStatus.ts
const labels: Record<string, string> = {
  queued: '等待沙箱执行服务',
  starting: '正在创建隔离运行环境',
  running: '沙箱运行中',
  waiting_approval: '等待人工确认',
  succeeded: '运行完成',
  failed: '运行失败',
  cancelled: '已取消',
};

export function runtimeStatusLabel(status?: string): string {
  if (!status) return '尚未启动运行';
  return labels[status] ?? `运行状态：${status}`;
}
```

- [ ] **Step 5: Refactor the chat view in small edits**

In `AgentConsoleView.vue`:

1. Replace fixed `sessions` with `store.conversations`.
2. Replace `sessionStorage` persistence with server load on mount.
3. Replace `sendMessage` timer with `store.sendMessage` and `store.replayEvents`.
4. Display `queued` as `等待沙箱执行服务`, not `沙箱已隔离`.
5. Display sandbox status only when supplied by a persisted RunEvent.
6. Keep the existing desktop three-column layout, mobile panel switching, and current user-authored styling.
7. Load single-Agent options from `agentsApi.list()`; remove the fixed `agentOptions` fallback.
8. Keep team mode visible but disabled with `发布型团队接入后可用`; remove fixed team members, online counts, and completed steps.
9. Keep knowledge-base and business-resource selectors visible but disabled with `能力接入后可用`; remove fixed selections and do not include them in the message request.
10. Keep attachment and `@成员` controls disabled until their server contracts exist.
11. Render no report or GIS action from message text; later Artifact events own those actions.
12. Show API/store errors near the work area without replacing the whole three-column layout.

Replace the local session/message persistence and simulated send block with this store adapter:

```typescript
import { computed, nextTick, onMounted, ref, useTemplateRef, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { agentsApi } from '@/api/agents';
import { runtimeStatusLabel } from '@/features/chat/runtimeStatus';
import { useConversationStore } from '@/stores/conversations';

const conversationStore = useConversationStore();
const {
  conversations: serverConversations,
  activeConversationId: selectedSessionId,
  activeRun,
  sending,
} = storeToRefs(conversationStore);

const sessions = computed(() => serverConversations.value.map((conversation) => ({
  id: conversation.id,
  title: conversation.title,
  summary: '持久化项目会话',
  time: new Date(conversation.updated_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit' }),
  mode: '运行会话',
})));
const filteredSessions = computed(() => {
  const term = historySearch.value.trim();
  return sessions.value.filter((session) => `${session.title}${session.summary}`.includes(term));
});

const messages = computed(() => conversationStore.messages.map((message) => ({
  id: message.id,
  role: message.role === 'user' ? 'user' as const : 'agent' as const,
  author: message.role === 'user' ? '当前用户' : activeActorName.value,
  avatar: message.role === 'user' ? '我' : mode.value === 'team' ? '协' : '智',
  time: new Date(message.created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
  content: message.content,
})));

const agentOptions = ref<Array<{ value: string; label: string }>>([]);
const selectedAgentId = ref('');
const actorLoadError = ref('');
const runtimeLabel = computed(() => runtimeStatusLabel(activeRun.value?.status));
const canSend = computed(() => Boolean(input.value.trim() && selectedAgentId.value && !sending.value));

async function selectSession(id: string) {
  await conversationStore.selectConversation(id);
  mobilePanel.value = 'chat';
}

function newConversation() {
  conversationStore.startNewConversation();
  input.value = '';
  mobilePanel.value = 'chat';
}

async function sendMessage() {
  const content = input.value.trim();
  if (!content || !selectedAgentId.value || sending.value) return;
  input.value = '';
  await conversationStore.sendMessage(content, 'agent', selectedAgentId.value);
}

onMounted(async () => {
  await conversationStore.loadConversations();
  try {
    const agents = await agentsApi.list();
    agentOptions.value = agents
      .filter((agent) => agent.enabled)
      .map((agent) => ({ value: agent.id, label: agent.name }));
    selectedAgentId.value = agentOptions.value[0]?.value ?? '';
  } catch (error) {
    actorLoadError.value = error instanceof Error ? error.message : '智能体列表加载失败';
  }
});
```

Change the top-bar runtime text to `{{ runtimeLabel }}` and bind the send button to `:disabled="!canSend"`. Render `actorLoadError` beside the execution-subject selector and leave sending disabled when no enabled Agent is available. Change history keys and selections from numeric IDs to strings, format `session.updated_at` in the template or a focused formatter, and remove `chatStateKey`, `readSavedState`, `persistChatState`, `initialMessages`, fixed sessions, fixed Agent/team/knowledge/resource data, fixed team steps, the message persistence watcher, and the 800ms timer. Keep the existing scroll watcher against the computed `messages`.

- [ ] **Step 6: Run tests and production build**

Run: `cd frontend; npm test`

Expected: all tests PASS.

Run: `cd frontend; npm run build`

Expected: build PASS with no TypeScript errors.

- [ ] **Step 7: Commit the real chat state**

```powershell
git add frontend/src/stores/conversations.ts frontend/src/stores/conversations.test.ts frontend/src/features/chat frontend/src/views/agent/AgentConsoleView.vue
git commit -m "feat: connect chat to persisted runs"
```

### Task 9: Document and Verify the Foundation

**Files:**
- Modify: `README.md`
- Modify: `backend/README.md`

- [ ] **Step 1: Document exact development commands**

Add:

```powershell
docker compose up -d postgres
$env:DATABASE_URL = "postgresql+psycopg://iap:iap@127.0.0.1:5432/iap"
$env:IAP_ALLOW_DEV_IDENTITY = "true"
cd backend
python -m alembic upgrade head
python -m uvicorn app.main:app --reload --port 8000
```

Document `VITE_DEV_USER_ID` and `VITE_DEV_PROJECT_ID` for local frontend development, and state clearly that the identity adapter is disabled by default and is not production authentication.

- [ ] **Step 2: Run the complete backend suite**

Run: `cd backend; python -m pytest -q`

Expected: all tests PASS.

- [ ] **Step 3: Run frontend tests and build**

Run: `cd frontend; npm test; npm run build`

Expected: all tests and build PASS.

- [ ] **Step 4: Run Compose smoke checks**

Run: `docker compose up -d --build postgres api web`

Run: `Invoke-WebRequest -UseBasicParsing http://127.0.0.1/api/health | Select-Object -ExpandProperty StatusCode`

Expected: `200`.

Create a conversation with development identity headers, post a message, and verify the returned Run is `queued`. Open the SSE URL with `Last-Event-ID: 0` and verify the persisted `run.status` event is returned.

- [ ] **Step 5: Confirm no false sandbox claim remains**

Run: `rg -n "沙箱已隔离|setTimeout\(resolve, 800\)|iap-prototype-chat-state" frontend/src/views/agent/AgentConsoleView.vue`

Expected: no matches.

- [ ] **Step 6: Verify the frozen prototype boundary in a browser**

Open `/chat` at desktop and mobile viewports and verify:

1. The three desktop columns and mobile `会话 / 对话 / 上下文` switching remain usable.
2. The Run label is `尚未启动运行` before a message and `等待沙箱执行服务` after the queued response.
3. No assistant reply, collaboration step, citation, report, GIS action, knowledge selection, or business-resource selection is fabricated.
4. Team mode, attachment, `@成员`, knowledge-base selection, and resource selection clearly show unavailable/disabled state.
5. Reloading restores conversations and messages from the API.

Capture desktop and mobile screenshots under `output/prototype-freeze-verification/` for local evidence; do not commit generated screenshots.

- [ ] **Step 7: Commit documentation**

```powershell
git add README.md backend/README.md
git commit -m "docs: document conversation run foundation"
```

## Completion Criteria

- Conversation and Run data persist in PostgreSQL and survive API restarts.
- Every private conversation and Run query is scoped by the request project and owner.
- Posting a message atomically creates Message, AgentRun, and the first RunEvent.
- SSE replay honors `Last-Event-ID` and emits stable event IDs.
- The frontend uses server conversations and no longer fabricates assistant replies.
- The UI does not claim sandbox isolation before a real sandbox event exists.
- Fixed Agent/team/knowledge/resource options, collaboration steps, citations, report actions, and GIS actions are absent from the production state.
- Unimplemented team, attachment, knowledge, resource, and Artifact capabilities are visibly disabled rather than silently simulated.
- The existing desktop and mobile chat layouts remain usable.
- Existing Agent, Skill, MCP, model-provider, and platform tests continue to pass.
- PostgreSQL migration, backend tests, frontend tests, frontend build, and Compose health check all pass.
