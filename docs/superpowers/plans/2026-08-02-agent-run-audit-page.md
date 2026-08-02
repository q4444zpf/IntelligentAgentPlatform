# Agent Run Audit Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `/runs` placeholder with a project-scoped, paginated Agent Run audit page that exposes safe run, event, and tool invocation details.

**Architecture:** Extend the existing conversations repository and service with one aggregated list query and a typed page response. Keep the existing run detail, SSE event, and tool invocation endpoints, then add a focused Vue API module and a lazy-loading detail drawer that follows the current console visual system.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2, Pydantic 2, Pytest, Vue 3 Composition API, TypeScript, Ant Design Vue, Vitest, Vue Test Utils.

---

## File Map

- Modify `backend/app/conversations/schemas.py`: define list query output types and summary types.
- Modify `backend/app/conversations/repository.py`: perform scoped filtering, stable pagination, aggregate counts, and safe trigger projection.
- Modify `backend/app/conversations/service.py`: map repository rows into public response models.
- Modify `backend/app/conversations/router.py`: expose `GET /api/agent-runs` with validated query parameters.
- Modify `backend/tests/conversations/test_repository.py`: cover stable ordering, filtering, counts, and data isolation.
- Modify `backend/tests/conversations/test_api.py`: cover the public list contract and invalid parameters.
- Create `frontend/src/api/agentRuns.ts`: hold Agent Run list/detail types and API calls.
- Create `frontend/src/api/agentRuns.test.ts`: verify query encoding and detail endpoint paths.
- Create `frontend/src/views/runs/AgentRunListView.vue`: render filters, server pagination, summary, and lazy detail drawer.
- Create `frontend/src/views/runs/AgentRunListView.test.ts`: cover data loading, filtering, details, errors, and route contract.
- Modify `frontend/src/router/routes.ts`: route `/runs` to the real page.

### Task 1: Backend Query Contract and Repository

**Files:**
- Modify: `backend/app/conversations/schemas.py`
- Modify: `backend/app/conversations/repository.py`
- Test: `backend/tests/conversations/test_repository.py`

- [ ] **Step 1: Write the failing repository tests**

Add a local seeded SQLite fixture and tests that prove scope, stable ordering, filters, safe summaries, pagination, and aggregate counts:

```python
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.conversations.models import AgentRun, Conversation, Message, ToolInvocation
from app.db.base import Base


def build_run_repository(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'run-list.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add_all([
        Conversation(id="c-new", project_id="p1", owner_id="u1", title="最新研判"),
        Conversation(id="c-old", project_id="p1", owner_id="u1", title="历史研判"),
        Conversation(id="c-other", project_id="p2", owner_id="u2", title="不可见"),
        Message(id="m-new", conversation_id="c-new", sequence=1, role="user", content="调用时间工具确认当前日期"),
        Message(id="m-old", conversation_id="c-old", sequence=1, role="user", content="分析洪峰"),
        Message(id="m-other", conversation_id="c-other", sequence=1, role="user", content="其他项目"),
        AgentRun(id="r-new", conversation_id="c-new", trigger_message_id="m-new", actor_type="agent", actor_id="platform-default-agent", status="completed", created_at=datetime(2026, 8, 2, 14, 0, tzinfo=timezone.utc), updated_at=datetime(2026, 8, 2, 14, 0, 1, tzinfo=timezone.utc)),
        AgentRun(id="r-old", conversation_id="c-old", trigger_message_id="m-old", actor_type="agent", actor_id="flood", status="failed", created_at=datetime(2026, 8, 1, 14, 0, tzinfo=timezone.utc), updated_at=datetime(2026, 8, 1, 14, 0, 2, tzinfo=timezone.utc)),
        AgentRun(id="r-other", conversation_id="c-other", trigger_message_id="m-other", actor_type="agent", actor_id="hidden", status="completed"),
        ToolInvocation(run_id="r-new", tool_call_id="call-1", tool_id="system.get_current_time", tool_version="1.0.0", status="completed", arguments_summary={}, result_summary={}, duration_ms=16),
        ToolInvocation(run_id="r-new", tool_call_id="call-2", tool_id="system.get_runtime_context", tool_version="1.0.0", status="completed", arguments_summary={}, result_summary={}, duration_ms=21),
    ])
    session.commit()
    return session, ConversationRepository(session)


def test_list_runs_is_scoped_ordered_and_aggregated(tmp_path):
    session, repository = build_run_repository(tmp_path)
    page = repository.list_runs(project_id="p1", owner_id="u1", page=1, page_size=20)
    assert [row["id"] for row in page.items] == ["r-new", "r-old"]
    assert page.items[0]["tool_invocation_count"] == 2
    assert page.items[0]["trigger_summary"] == "调用时间工具确认当前日期"
    assert page.total == 2
    assert page.summary == {"total": 2, "completed": 1, "running": 0, "failed": 1, "tool_invocations": 2}
    session.close()


def test_list_runs_applies_actor_status_query_time_and_pagination(tmp_path):
    session, repository = build_run_repository(tmp_path)
    page = repository.list_runs(
        project_id="p1",
        owner_id="u1",
        page=1,
        page_size=1,
        status="completed",
        actor_id="platform-default-agent",
        query="最新研判",
        started_after=datetime(2026, 8, 2, 0, 0, tzinfo=timezone.utc),
        started_before=datetime(2026, 8, 3, 0, 0, tzinfo=timezone.utc),
    )
    assert [row["id"] for row in page.items] == ["r-new"]
    assert page.total == 1
    session.close()
```

- [ ] **Step 2: Run the repository tests and verify RED**

Run:

```powershell
cd backend
python -m pytest tests/conversations/test_repository.py -q
```

Expected: FAIL because `ConversationRepository.list_runs` does not exist.

- [ ] **Step 3: Add repository result types and minimal aggregated query**

Add focused internal types in `repository.py`:

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class RunListResult:
    items: list[dict[str, Any]]
    total: int
    summary: dict[str, int]
```

Implement `list_runs(...)` with reusable scope predicates:

```python
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
    tool_count = (
        select(ToolInvocation.run_id, func.count(ToolInvocation.id).label("tool_count"))
        .group_by(ToolInvocation.run_id)
        .subquery()
    )
    predicates = [
        Conversation.project_id == project_id,
        Conversation.owner_id == owner_id,
    ]
    if status:
        predicates.append(AgentRun.status == status)
    if actor_id:
        predicates.append(AgentRun.actor_id == actor_id)
    if query:
        pattern = f"%{query.strip()}%"
        predicates.append((AgentRun.id.ilike(pattern)) | (Conversation.title.ilike(pattern)))
    if started_after:
        predicates.append(AgentRun.created_at >= started_after)
    if started_before:
        predicates.append(AgentRun.created_at <= started_before)

    base = (
        select(
            AgentRun.id,
            AgentRun.conversation_id,
            Conversation.title.label("conversation_title"),
            AgentRun.trigger_message_id,
            Message.content.label("trigger_content"),
            AgentRun.actor_type,
            AgentRun.actor_id,
            AgentRun.status,
            func.coalesce(tool_count.c.tool_count, 0).label("tool_invocation_count"),
            AgentRun.created_at,
            AgentRun.updated_at,
        )
        .join(Conversation, Conversation.id == AgentRun.conversation_id)
        .join(Message, Message.id == AgentRun.trigger_message_id)
        .outerjoin(tool_count, tool_count.c.run_id == AgentRun.id)
        .where(*predicates)
    )
    total = int(self.session.scalar(select(func.count()).select_from(base.subquery())) or 0)
    rows = self.session.execute(
        base.order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).mappings()
    items = []
    for row in rows:
        item = dict(row)
        item["trigger_summary"] = " ".join(item.pop("trigger_content").split())[:200]
        item["duration_ms"] = max(0, int((item["updated_at"] - item["created_at"]).total_seconds() * 1000))
        items.append(item)

    scoped = base.subquery()
    summary_row = self.session.execute(select(
        func.count(scoped.c.id).label("total"),
        func.count().filter(scoped.c.status == "completed").label("completed"),
        func.count().filter(scoped.c.status.in_(("queued", "running"))).label("running"),
        func.count().filter(scoped.c.status == "failed").label("failed"),
        func.coalesce(func.sum(scoped.c.tool_invocation_count), 0).label("tool_invocations"),
    )).mappings().one()
    return RunListResult(items=items, total=total, summary={key: int(summary_row[key]) for key in summary_row})
```

If SQLite rejects aggregate `FILTER`, replace only those four expressions with `func.sum(case((condition, 1), else_=0))`; keep the public result unchanged.

- [ ] **Step 4: Run repository tests and verify GREEN**

Run `python -m pytest tests/conversations/test_repository.py -q` from `backend`.

Expected: all repository tests PASS.

- [ ] **Step 5: Commit repository behavior**

```powershell
git add backend/app/conversations/repository.py backend/tests/conversations/test_repository.py
git commit -m "feat: query scoped agent runs"
```

### Task 2: Backend Service and HTTP API

**Files:**
- Modify: `backend/app/conversations/schemas.py`
- Modify: `backend/app/conversations/service.py`
- Modify: `backend/app/conversations/router.py`
- Test: `backend/tests/conversations/test_api.py`

- [ ] **Step 1: Write failing API contract tests**

Append tests using the existing `build_client()` helper:

```python
def create_run(client, *, title="洪水研判", actor_id="flood", content="分析洪峰"):
    conversation = client.post("/api/conversations", json={"title": title}, headers=HEADERS).json()
    return client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": content, "actor_type": "agent", "actor_id": actor_id},
        headers=HEADERS,
    ).json()["run"]


def test_list_agent_runs_returns_page_summary_and_safe_projection():
    client = build_client()
    run = create_run(client, title="时间验收", content="确认当前时间")
    response = client.get("/api/agent-runs?page=1&page_size=20", headers=HEADERS)
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["id"] == run["id"]
    assert payload["items"][0]["conversation_title"] == "时间验收"
    assert payload["items"][0]["trigger_summary"] == "确认当前时间"
    assert payload["summary"]["total"] == 1


def test_list_agent_runs_rejects_invalid_pagination_and_hides_other_scope():
    client = build_client()
    create_run(client)
    assert client.get("/api/agent-runs?page=0", headers=HEADERS).status_code == 422
    hidden = client.get("/api/agent-runs", headers={"X-User-ID": "u2", "X-Project-ID": "p2"})
    assert hidden.status_code == 200
    assert hidden.json()["items"] == []
```

- [ ] **Step 2: Run API tests and verify RED**

Run `python -m pytest tests/conversations/test_api.py -q` from `backend`.

Expected: FAIL with HTTP 404 for `GET /api/agent-runs`.

- [ ] **Step 3: Define the response schemas**

Add to `schemas.py`:

```python
class AgentRunListItem(BaseModel):
    id: str
    conversation_id: str
    conversation_title: str
    trigger_message_id: str
    trigger_summary: str
    actor_type: Literal["agent", "team"]
    actor_id: str
    status: str
    tool_invocation_count: int
    duration_ms: int
    created_at: datetime
    updated_at: datetime


class AgentRunSummary(BaseModel):
    total: int
    completed: int
    running: int
    failed: int
    tool_invocations: int


class AgentRunPage(BaseModel):
    items: list[AgentRunListItem]
    page: int
    page_size: int
    total: int
    summary: AgentRunSummary
```

- [ ] **Step 4: Add service mapping and validated route**

Add to `ConversationService`:

```python
def list_runs(self, context: RequestContext, **filters) -> AgentRunPage:
    result = self.repository.list_runs(
        project_id=context.project_id,
        owner_id=context.user_id,
        **filters,
    )
    return AgentRunPage(
        items=[AgentRunListItem.model_validate(item) for item in result.items],
        total=result.total,
        summary=AgentRunSummary.model_validate(result.summary),
        page=filters["page"],
        page_size=filters["page_size"],
    )
```

Add before the parameterized `/agent-runs/{run_id}` route in `router.py`:

```python
@router.get("/agent-runs", response_model=AgentRunPage)
def list_runs(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: str | None = None,
    actor_id: str | None = None,
    query: str | None = None,
    started_after: datetime | None = None,
    started_before: datetime | None = None,
    context: RequestContext = Depends(require_request_context),
    manager: ConversationService = Depends(service),
):
    return manager.list_runs(
        context,
        page=page,
        page_size=page_size,
        status=status,
        actor_id=actor_id,
        query=query,
        started_after=started_after,
        started_before=started_before,
    )
```

Import `datetime`, `Query`, `AgentRunPage`, `AgentRunListItem`, and `AgentRunSummary` in their owning modules.

- [ ] **Step 5: Run focused and full backend tests**

```powershell
cd backend
python -m pytest tests/conversations/test_repository.py tests/conversations/test_api.py -q
python -m pytest -q
```

Expected: focused tests PASS; full suite PASS with only the existing PostgreSQL test skip when `TEST_DATABASE_URL` is absent.

- [ ] **Step 6: Commit the API**

```powershell
git add backend/app/conversations/schemas.py backend/app/conversations/service.py backend/app/conversations/router.py backend/tests/conversations/test_api.py
git commit -m "feat: expose agent run audit API"
```

### Task 3: Frontend Agent Run API Client

**Files:**
- Create: `frontend/src/api/agentRuns.ts`
- Create: `frontend/src/api/agentRuns.test.ts`

- [ ] **Step 1: Write failing API client tests**

```typescript
import { afterEach, describe, expect, it, vi } from 'vitest';
import { agentRunsApi } from './agentRuns';

afterEach(() => vi.restoreAllMocks());

describe('agentRunsApi', () => {
  it('encodes list filters without empty values', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ items: [], page: 1, page_size: 20, total: 0, summary: { total: 0, completed: 0, running: 0, failed: 0, tool_invocations: 0 } })));
    await agentRunsApi.list({ page: 1, page_size: 20, status: 'completed', actor_id: '', query: '时间 验收' });
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('/agent-runs?page=1&page_size=20&status=completed&query=%E6%97%B6%E9%97%B4+%E9%AA%8C%E6%94%B6');
    expect(String(fetchMock.mock.calls[0]?.[0])).not.toContain('actor_id');
  });

  it('uses encoded run ids for detail resources', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('[]'));
    await agentRunsApi.listInvocations('run/a');
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('/agent-runs/run%2Fa/tool-invocations');
  });
});
```

- [ ] **Step 2: Run test and verify RED**

Run `npm test -- src/api/agentRuns.test.ts` from `frontend`.

Expected: FAIL because `./agentRuns` does not exist.

- [ ] **Step 3: Implement typed API calls**

Create `agentRuns.ts` with `AgentRunListItem`, `AgentRunSummary`, `AgentRunPage`, `AgentRunFilters`, `RunEventInfo`, and the existing `ToolInvocationInfo` import. Use `URLSearchParams` and the shared `request` helper:

```typescript
import { request } from './client';
import type { AgentRunInfo } from './conversations';
import type { ToolInvocationInfo } from './tools';

export interface AgentRunFilters {
  page: number;
  page_size: number;
  status?: string;
  actor_id?: string;
  query?: string;
  started_after?: string;
  started_before?: string;
}

export interface AgentRunListItem extends AgentRunInfo {
  conversation_title: string;
  trigger_summary: string;
  tool_invocation_count: number;
  duration_ms: number;
}

export interface AgentRunPage {
  items: AgentRunListItem[];
  page: number;
  page_size: number;
  total: number;
  summary: { total: number; completed: number; running: number; failed: number; tool_invocations: number };
}

export interface RunEventInfo {
  sequence: number;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

function queryString(filters: AgentRunFilters) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== '') params.set(key, String(value));
  });
  return params.toString();
}

export const agentRunsApi = {
  list: (filters: AgentRunFilters, signal?: AbortSignal) => request<AgentRunPage>(`/agent-runs?${queryString(filters)}`, { signal }),
  get: (runId: string, signal?: AbortSignal) => request<AgentRunInfo>(`/agent-runs/${encodeURIComponent(runId)}`, { signal }),
  listEvents: (runId: string, signal?: AbortSignal) => request<RunEventInfo[]>(`/agent-runs/${encodeURIComponent(runId)}/events`, { signal, headers: { 'Last-Event-ID': '0' } }),
  listInvocations: (runId: string, signal?: AbortSignal) => request<ToolInvocationInfo[]>(`/agent-runs/${encodeURIComponent(runId)}/tool-invocations`, { signal }),
};
```

Because the existing events endpoint uses SSE framing, either add `GET /api/agent-runs/{run_id}/events.json` in Task 2 or reuse `getRunEvents(runId, 0)` from `runEvents.ts`; prefer reuse and change `listEvents` above to delegate to `getRunEvents` while mapping `created_at` as optional. Do not parse SSE twice.

- [ ] **Step 4: Run API tests and verify GREEN**

Run `npm test -- src/api/agentRuns.test.ts` from `frontend`.

Expected: 2 tests PASS.

- [ ] **Step 5: Commit the frontend API client**

```powershell
git add frontend/src/api/agentRuns.ts frontend/src/api/agentRuns.test.ts
git commit -m "feat: add agent run audit client"
```

### Task 4: Agent Run List and Detail Drawer

**Files:**
- Create: `frontend/src/views/runs/AgentRunListView.vue`
- Create: `frontend/src/views/runs/AgentRunListView.test.ts`
- Modify: `frontend/src/router/routes.ts`

- [ ] **Step 1: Write failing view behavior tests**

Mock `agentRunsApi` and mount with lightweight Ant Design stubs. Cover these independent behaviors:

```typescript
// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AgentRunListView from './AgentRunListView.vue';

const mocks = vi.hoisted(() => ({ list: vi.fn(), get: vi.fn(), listEvents: vi.fn(), listInvocations: vi.fn() }));
vi.mock('@/api/agentRuns', () => ({ agentRunsApi: mocks }));

const page = {
  items: [{ id: 'r1', conversation_id: 'c1', conversation_title: '时间验收', trigger_message_id: 'm1', trigger_summary: '确认时间', actor_type: 'agent', actor_id: 'platform-default-agent', status: 'completed', tool_invocation_count: 2, duration_ms: 1200, created_at: '2026-08-02T14:38:00Z', updated_at: '2026-08-02T14:38:01Z' }],
  page: 1,
  page_size: 20,
  total: 1,
  summary: { total: 1, completed: 1, running: 0, failed: 0, tool_invocations: 2 },
};

beforeEach(() => {
  Object.values(mocks).forEach((mock) => mock.mockReset());
  mocks.list.mockResolvedValue(page);
  mocks.get.mockResolvedValue(page.items[0]);
  mocks.listEvents.mockResolvedValue([{ sequence: 1, event_type: 'run.status', payload: { status: 'completed' } }]);
  mocks.listInvocations.mockResolvedValue([{ id: 'i1', run_id: 'r1', tool_call_id: 'c1', tool_id: 'system.get_current_time', tool_version: '1.0.0', status: 'completed', arguments_summary: {}, result_summary: {}, duration_ms: 16, error_code: null, created_at: '2026-08-02T14:38:00Z', completed_at: '2026-08-02T14:38:00Z' }]);
});

it('loads and renders scoped runs and summary', async () => {
  const wrapper = mount(AgentRunListView, { global: { stubs } });
  await flushPromises();
  expect(mocks.list).toHaveBeenCalledWith(expect.objectContaining({ page: 1, page_size: 20 }), expect.any(AbortSignal));
  expect(wrapper.text()).toContain('时间验收');
  expect(wrapper.text()).toContain('工具调用');
});

it('loads detail resources only after opening a run', async () => {
  const wrapper = mount(AgentRunListView, { global: { stubs } });
  await flushPromises();
  expect(mocks.listInvocations).not.toHaveBeenCalled();
  await wrapper.get('[aria-label="查看运行 r1"]').trigger('click');
  await flushPromises();
  expect(mocks.get).toHaveBeenCalledWith('r1', expect.any(AbortSignal));
  expect(mocks.listEvents).toHaveBeenCalledWith('r1', expect.any(AbortSignal));
  expect(mocks.listInvocations).toHaveBeenCalledWith('r1', expect.any(AbortSignal));
  expect(wrapper.text()).toContain('system.get_current_time');
});

it('shows a retryable list error', async () => {
  mocks.list.mockRejectedValueOnce(new Error('运行记录服务不可用')).mockResolvedValueOnce(page);
  const wrapper = mount(AgentRunListView, { global: { stubs } });
  await flushPromises();
  expect(wrapper.text()).toContain('运行记录服务不可用');
  await wrapper.get('[aria-label="重试运行列表"]').trigger('click');
  await flushPromises();
  expect(mocks.list).toHaveBeenCalledTimes(2);
});
```

Define `stubs` for `a-alert`, `a-button`, `a-card`, `a-date-picker`, `a-descriptions`, `a-drawer`, `a-empty`, `a-input-search`, `a-pagination`, `a-select`, `a-spin`, `a-table`, `a-tag`, and `a-timeline`. The table stub must expose each item through a slot or simple row button so tests exercise the component rather than only raw source.

- [ ] **Step 2: Run the view test and verify RED**

Run `npm test -- src/views/runs/AgentRunListView.test.ts` from `frontend`.

Expected: FAIL because `AgentRunListView.vue` does not exist.

- [ ] **Step 3: Implement the minimal page behavior**

Create a Composition API component with these state boundaries:

```typescript
const loading = ref(false);
const loadError = ref('');
const pageData = ref<AgentRunPage>(emptyPage());
const filters = reactive<AgentRunFilters>({ page: 1, page_size: 20, status: '', actor_id: '', query: '' });
const detailOpen = ref(false);
const selectedRun = ref<AgentRunInfo | null>(null);
const events = ref<RunEventInfo[]>([]);
const invocations = ref<ToolInvocationInfo[]>([]);
const detailErrors = reactive({ run: '', events: '', invocations: '' });
let listController: AbortController | null = null;
let detailController: AbortController | null = null;

async function loadRuns() {
  listController?.abort();
  const controller = new AbortController();
  listController = controller;
  loading.value = true;
  loadError.value = '';
  try {
    pageData.value = await agentRunsApi.list({ ...filters }, controller.signal);
  } catch (error) {
    if (!controller.signal.aborted) loadError.value = error instanceof Error ? error.message : '运行记录加载失败';
  } finally {
    if (listController === controller) loading.value = false;
  }
}

async function openRun(item: AgentRunListItem) {
  detailController?.abort();
  const controller = new AbortController();
  detailController = controller;
  detailOpen.value = true;
  selectedRun.value = item;
  events.value = [];
  invocations.value = [];
  Object.assign(detailErrors, { run: '', events: '', invocations: '' });
  const results = await Promise.allSettled([
    agentRunsApi.get(item.id, controller.signal),
    agentRunsApi.listEvents(item.id, controller.signal),
    agentRunsApi.listInvocations(item.id, controller.signal),
  ]);
  if (controller.signal.aborted) return;
  if (results[0].status === 'fulfilled') selectedRun.value = results[0].value; else detailErrors.run = errorMessage(results[0].reason);
  if (results[1].status === 'fulfilled') events.value = results[1].value; else detailErrors.events = errorMessage(results[1].reason);
  if (results[2].status === 'fulfilled') invocations.value = results[2].value; else detailErrors.invocations = errorMessage(results[2].reason);
}

onMounted(loadRuns);
onUnmounted(() => { listController?.abort(); detailController?.abort(); });
```

Template requirements:

- Use one unframed `.run-page` grid with a compact white heading band, matching `ToolManageView.vue` colors and radii.
- Render five summary cells, a compact filter bar, and an `a-table` with stable `row-key="id"`.
- Add an icon-only refresh button with `aria-label="刷新运行列表"` and tooltip.
- Add one row action with `:aria-label="`查看运行 ${record.id}`"`.
- Render the detail drawer with descriptions, timeline, and invocation list; use `a-empty` for no invocations.
- Render JSON summaries with `JSON.stringify(value, null, 2)` inside escaped `<pre>` text only.
- Keep responsive constraints at 1100px and 700px; do not introduce new global tokens or nested cards.

- [ ] **Step 4: Update route and contract test**

Change the `/runs` route to:

```typescript
{ path: 'runs', name: 'AgentRuns', component: () => import('@/views/runs/AgentRunListView.vue'), meta: { title: 'Agent Runs', permission: 'platform:view' } },
```

Add a test assertion that `routes.ts?raw` contains `@/views/runs/AgentRunListView.vue` and does not assign `module: 'collaboration'` on the AgentRuns route.

- [ ] **Step 5: Run focused tests and iterate to GREEN**

```powershell
cd frontend
npm test -- src/api/agentRuns.test.ts src/views/runs/AgentRunListView.test.ts
```

Expected: all new frontend tests PASS with no unhandled promise errors.

- [ ] **Step 6: Run full frontend verification**

```powershell
npm test
npm run build
```

Expected: full Vitest suite PASS and Vite production build succeeds. The existing bundle-size warning is acceptable.

- [ ] **Step 7: Commit the page**

```powershell
git add frontend/src/views/runs/AgentRunListView.vue frontend/src/views/runs/AgentRunListView.test.ts frontend/src/router/routes.ts
git commit -m "feat: add agent run audit page"
```

### Task 5: Integrated Verification and Documentation

**Files:**
- Modify: `README.md`
- Modify: `frontend/README.md`
- Modify: `backend/README.md`

- [ ] **Step 1: Add the new API and page to module documentation**

Document `GET /api/agent-runs`, its project/user scope, the `/runs` page, and the fact that `/system/audit` remains the future cross-source audit center. Do not document any real credential or database password.

- [ ] **Step 2: Run complete automated verification**

```powershell
cd backend
python -m pytest -q
cd ..\frontend
npm test
npm run build
```

Expected: backend suite PASS with only the known PostgreSQL integration skip when unconfigured; frontend tests and production build PASS.

- [ ] **Step 3: Start local services and perform browser acceptance**

Use the repository `start-dev.ps1` workflow or the already running PostgreSQL/API/Web services. In the browser:

1. Send `请调用平台工具确认当前日期、星期和当前项目，只返回核验结果。` from `/chat`.
2. Open `/runs`.
3. Filter by `platform-default-agent` and `completed`.
4. Open the newest matching Run.
5. Verify `system.get_current_time` and `system.get_runtime_context` are completed and their displayed durations match `GET /api/agent-runs/{run_id}/tool-invocations`.
6. Verify the browser console has no errors and the desktop layout has no overlap.

- [ ] **Step 4: Check the final diff and repository state**

```powershell
git diff --check
git status --short
git log --oneline -5
```

Expected: no whitespace errors; only pre-existing ignored or untracked test caches remain outside the feature commits.

- [ ] **Step 5: Commit documentation**

```powershell
git add README.md frontend/README.md backend/README.md
git commit -m "docs: document agent run audit"
```

Do not push or merge until the user explicitly requests it.
