# Unified Audit Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a real `/system/audit` center that stores append-only, redacted runtime and management events with unit/project/user isolation and links Agent events back to `/runs`.

**Architecture:** Add unit scope to conversation execution context and a PostgreSQL `audit_events` write model. A focused audit package owns redaction, idempotent recording, policy-scoped queries, schemas, and API routes; runtime and management services call the recorder through explicit transaction boundaries. The Vue page consumes the stable API with server pagination and lazy related-event details.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, PostgreSQL, Pydantic v2, pytest, Vue 3, TypeScript, Ant Design Vue, Vitest

---

## Scope Decomposition

This plan implements the approved first version in five dependent increments:

1. Unit-aware identity and persistence.
2. Audit write/query foundation and API.
3. Agent, tool, LLM, and management event producers.
4. Unified Vue audit page and Agent Run linking.
5. PostgreSQL, browser, documentation, and regression verification.

Knowledge retrieval, real MCP execution, sandbox execution, export jobs, retention workers, and an event bus remain outside this plan. Their future adapters must call the same `AuditRecorder`.

## File Map

**Create:**

- `backend/app/audit/__init__.py` — audit package exports.
- `backend/app/audit/models.py` — append-only `AuditEvent` model.
- `backend/app/audit/redaction.py` — bounded schema-aware redaction.
- `backend/app/audit/recorder.py` — idempotent event append interface.
- `backend/app/audit/policy.py` — role-to-SQL scope construction.
- `backend/app/audit/repository.py` — list, summary, detail, and related queries.
- `backend/app/audit/schemas.py` — request/response contracts and enums.
- `backend/app/audit/service.py` — policy-scoped application service.
- `backend/app/audit/router.py` — `/api/audit` endpoints.
- `backend/app/audit/backfill.py` — idempotent legacy Agent Run snapshots.
- `backend/alembic/versions/20260803_06_unified_audit.py` — unit and audit schema migration.
- `backend/tests/audit/test_redaction.py`
- `backend/tests/audit/test_recorder.py`
- `backend/tests/audit/test_repository.py`
- `backend/tests/audit/test_service.py`
- `backend/tests/audit/test_api.py`
- `backend/tests/audit/test_backfill.py`
- `frontend/src/api/audit.ts`
- `frontend/src/api/audit.test.ts`
- `frontend/src/views/security/AuditLogView.vue`
- `frontend/src/views/security/AuditLogView.test.ts`

**Modify:**

- `backend/app/core/request_context.py` — unit and role-set identity.
- `backend/app/conversations/models.py` — persist conversation unit.
- `backend/app/conversations/repository.py` — unit-scoped reads and execution context.
- `backend/app/conversations/service.py` — record Run creation.
- `backend/app/runtime/harness.py` — record Run and LLM outcomes.
- `backend/app/tools/gateway.py` — record tool start/outcome.
- `backend/app/db/base.py` — import audit model metadata.
- `backend/app/main.py` — mount audit router.
- `backend/app/agents/router.py`, `service.py`, `store.py` — authenticated transactional management events.
- `backend/app/tools/router.py`, `service.py`, `store.py` — tool management events.
- `backend/app/mcp/router.py`, `service.py`, `store.py` — MCP management events.
- `backend/app/model_providers/router.py`, `service.py`, `store.py` — model management events.
- Existing backend tests that construct `RequestContext` or `Conversation`.
- `backend/tests/integration/test_postgres_migrations.py` — audit table/index assertions.
- `frontend/src/api/client.ts` — dev unit/role headers.
- `frontend/src/router/routes.ts` — real lazy audit view.
- `frontend/src/views/runs/AgentRunListView.vue` — open a requested Run from query.
- `frontend/src/views/runs/AgentRunListView.test.ts` — deep-link behavior.
- `backend/README.md`, `frontend/README.md`, `.env.example` — contract and configuration.

### Task 1: Add Unit-Aware Request Context and Conversation Scope

**Files:**

- Modify: `backend/app/core/request_context.py`
- Modify: `backend/app/conversations/models.py`
- Modify: `backend/app/conversations/repository.py`
- Modify: `backend/app/conversations/service.py`
- Modify: `backend/tests/core/test_request_context.py`
- Modify: `backend/tests/conversations/test_api.py`
- Modify: `backend/tests/conversations/test_repository.py`
- Modify: `frontend/src/api/client.ts`
- Modify: `.env.example`

- [ ] **Step 1: Write failing identity and isolation tests**

Add cases proving the dev identity requires `X-Unit-ID`, maps legacy `admin` to `project_admin`, accepts comma-separated roles, and prevents the same project/user IDs in another unit from reading conversations or Runs.

```python
UNIT_HEADERS = {
    "X-Unit-ID": "unit-1",
    "X-Project-ID": "project-1",
    "X-User-ID": "user-1",
    "X-User-Roles": "project_admin,user",
}

def test_builds_unit_scoped_role_set(client):
    response = client.get("/context", headers=UNIT_HEADERS)
    assert response.json() == {
        "unit_id": "unit-1",
        "project_id": "project-1",
        "user_id": "user-1",
        "roles": ["project_admin", "user"],
    }

def test_other_unit_cannot_read_same_project_and_user(client):
    run_id = create_run(client, headers=UNIT_HEADERS)
    response = client.get(
        f"/api/agent-runs/{run_id}",
        headers=UNIT_HEADERS | {"X-Unit-ID": "unit-2"},
    )
    assert response.status_code == 404
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path backend)
uv run pytest backend/tests/core/test_request_context.py backend/tests/conversations/test_api.py backend/tests/conversations/test_repository.py -q
```

Expected: FAIL because `RequestContext` and `Conversation` do not contain `unit_id`.

- [ ] **Step 3: Implement the identity contract**

Use a role set while preserving a compatibility property for code that still checks `context.role` during this task:

```python
AuditRole = Literal["user", "project_admin", "unit_auditor"]

class RequestContext(BaseModel):
    unit_id: str
    project_id: str
    user_id: str
    roles: frozenset[AuditRole] = frozenset({"user"})

    @property
    def role(self) -> Literal["user", "admin"]:
        return "admin" if "project_admin" in self.roles or "unit_auditor" in self.roles else "user"
```

For dev headers, require `X-Unit-ID`, `X-Project-ID`, and `X-User-ID`; parse `X-User-Roles`. If only legacy `X-User-Role: admin` exists, map it to `project_admin`. Reject unknown roles with 401.

Add `Conversation.unit_id`, write it in `create_conversation`, include it in `get_run_execution_context`, and add `unit_id` to every conversation and Run scope predicate.

Update frontend dev headers:

```ts
'X-Unit-ID': import.meta.env.VITE_DEV_UNIT_ID,
'X-User-Roles': import.meta.env.VITE_DEV_USER_ROLES,
```

- [ ] **Step 4: Run focused backend and frontend client tests**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path backend)
uv run pytest backend/tests/core/test_request_context.py backend/tests/conversations -q
cd frontend
npm test -- src/api/client.test.ts
```

Expected: all selected tests PASS.

- [ ] **Step 5: Commit**

```bash
git add .env.example backend/app/core/request_context.py backend/app/conversations frontend/src/api/client.ts backend/tests/core backend/tests/conversations frontend/src/api/client.test.ts
git commit -m "feat: add unit-aware request context"
```

### Task 2: Add the Audit Schema Migration and Model

**Files:**

- Create: `backend/alembic/versions/20260803_06_unified_audit.py`
- Create: `backend/app/audit/__init__.py`
- Create: `backend/app/audit/models.py`
- Modify: `backend/app/db/base.py`
- Modify: `backend/tests/integration/test_postgres_migrations.py`
- Modify: `backend/tests/conversations/test_models.py`

- [ ] **Step 1: Extend migration tests**

Assert `conversations.unit_id` is non-null, `audit_events` exists, `idempotency_key` is unique, and the required composite indexes exist.

```python
audit_columns = {c["name"]: c for c in inspector.get_columns("audit_events")}
assert audit_columns["unit_id"]["nullable"] is False
assert audit_columns["metadata_json"]["nullable"] is False
assert inspector.get_unique_constraints("audit_events")[0]["column_names"] == ["idempotency_key"]
assert {
    "ix_audit_unit_time",
    "ix_audit_project_time",
    "ix_audit_user_time",
    "ix_audit_trace_time",
    "ix_audit_run_time",
    "ix_audit_source_action_status",
} <= {item["name"] for item in inspector.get_indexes("audit_events")}
```

- [ ] **Step 2: Run the migration test and verify failure**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path backend)
uv run pytest backend/tests/integration/test_postgres_migrations.py -q
```

Expected: FAIL or SKIP locally; when `TEST_DATABASE_URL` is configured it fails because revision `20260803_06` is absent.

- [ ] **Step 3: Implement migration and model**

The migration must:

1. Add nullable `conversations.unit_id`.
2. Backfill existing rows with `legacy-unit`.
3. Make the column non-null and add `ix_conversations_unit_project_owner`.
4. Create `audit_events` with the exact approved fields.
5. Add the unique constraint and named indexes.
6. Downgrade by dropping audit indexes/table, conversation index, then `unit_id`.

Model core:

```python
class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_audit_idempotency_key"),
        Index("ix_audit_unit_time", "unit_id", "occurred_at", "id"),
        Index("ix_audit_project_time", "unit_id", "project_id", "occurred_at", "id"),
        Index("ix_audit_user_time", "unit_id", "project_id", "user_id", "occurred_at", "id"),
        Index("ix_audit_trace_time", "trace_id", "occurred_at", "id"),
        Index("ix_audit_run_time", "run_id", "occurred_at", "id"),
        Index("ix_audit_source_action_status", "source", "action", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    unit_id: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
```

Add every field from the approved spec, using timezone-aware timestamps and no update timestamp. Import the model from `app/db/base.py`.

- [ ] **Step 4: Run model and migration checks**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path backend)
uv run pytest backend/tests/conversations/test_models.py backend/tests/integration/test_postgres_migrations.py -q
uv run alembic -c backend/alembic.ini heads
```

Expected: model tests PASS, one Alembic head is `20260803_06`; PostgreSQL test PASS when configured or reports its explicit skip.

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/20260803_06_unified_audit.py backend/app/audit backend/app/db/base.py backend/tests/integration backend/tests/conversations/test_models.py
git commit -m "feat: add unified audit schema"
```

### Task 3: Implement Redaction and Idempotent Recording

**Files:**

- Create: `backend/app/audit/redaction.py`
- Create: `backend/app/audit/recorder.py`
- Create: `backend/tests/audit/test_redaction.py`
- Create: `backend/tests/audit/test_recorder.py`

- [ ] **Step 1: Write redaction and duplicate-delivery tests**

```python
def test_redacts_nested_secrets_and_paths():
    value = redact_metadata({
        "authorization": "Bearer secret",
        "nested": {"api_key": "secret", "safe": "ok"},
        "env": {"TOKEN": "secret"},
        "path": "C:/customer/private/model.py",
    }, allowed_keys={"authorization", "nested", "env", "path"})
    assert value["authorization"] == "[REDACTED]"
    assert value["nested"]["api_key"] == "[REDACTED]"
    assert value["nested"]["safe"] == "ok"
    assert value["env"] == "[REDACTED]"
    assert "customer" not in value["path"]

def test_duplicate_idempotency_key_returns_existing(session):
    first = recorder.record(session, request)
    second = recorder.record(session, request)
    assert second.id == first.id
    assert session.query(AuditEvent).count() == 1
```

Also test unknown metadata keys are removed, depth/list/key/string/byte limits apply, and no raw HTML is introduced.

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path backend)
uv run pytest backend/tests/audit/test_redaction.py backend/tests/audit/test_recorder.py -q
```

Expected: FAIL because the audit helpers do not exist.

- [ ] **Step 3: Implement bounded redaction**

Expose:

```python
def redact_summary(value: str, *, max_chars: int = 500) -> str: ...
def redact_metadata(
    value: Mapping[str, Any],
    *,
    allowed_keys: Collection[str],
    max_bytes: int = 4096,
) -> dict[str, Any]: ...
```

Match sensitive keys case-insensitively, redact `env` as a whole, replace absolute paths with a basename or Artifact ID, bound recursion to 5, dict keys to 50, arrays to 20, and strings to 512 characters.

- [ ] **Step 4: Implement AuditRecordRequest and recorder**

```python
@dataclass(frozen=True)
class AuditRecordRequest:
    unit_id: str
    category: AuditCategory
    source: AuditSource
    action: str
    status: AuditStatus
    risk_level: AuditRisk
    idempotency_key: str
    occurred_at: datetime
    project_id: str | None = None
    user_id: str | None = None
    actor_role: str | None = None
    trace_id: str | None = None
    run_id: str | None = None
    parent_event_id: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    resource_name: str | None = None
    summary: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    allowed_metadata_keys: frozenset[str] = frozenset()
    error_code: str | None = None
    duration_ms: int | None = None
```

`record(session, request)` validates timezone, action/idempotency lengths and enum values, sanitizes content, flushes, catches only the named idempotency unique conflict, rolls back the nested savepoint, and returns the existing row. It must not commit; the caller owns the transaction.

- [ ] **Step 5: Run tests and commit**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path backend)
uv run pytest backend/tests/audit/test_redaction.py backend/tests/audit/test_recorder.py -q
```

Expected: PASS.

```bash
git add backend/app/audit backend/tests/audit
git commit -m "feat: record redacted audit events"
```

### Task 4: Implement Scoped Query Policy, Repository, Service, and API

**Files:**

- Create: `backend/app/audit/policy.py`
- Create: `backend/app/audit/repository.py`
- Create: `backend/app/audit/schemas.py`
- Create: `backend/app/audit/service.py`
- Create: `backend/app/audit/router.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/audit/test_repository.py`
- Create: `backend/tests/audit/test_service.py`
- Create: `backend/tests/audit/test_api.py`
- Modify: `backend/tests/test_main.py`

- [ ] **Step 1: Write role-scope and aggregation tests**

Seed two units, two projects, two users, a shared Trace ID, literal `%_\\` resource names, and multiple statuses. Prove:

- Unit auditor sees only its unit.
- Project admin sees only its current project.
- User sees only its own rows.
- Detail and related Trace never leak rows outside list scope.
- Summary is computed over the complete filtered set, not the current page.
- Query wildcard characters are literal.
- Invalid enum, timezone-free dates, reversed dates, page 0, and page size 101 return 422.
- Missing/unauthorized event returns the safe 404 text.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path backend)
uv run pytest backend/tests/audit/test_repository.py backend/tests/audit/test_service.py backend/tests/audit/test_api.py -q
```

Expected: FAIL because query components are absent.

- [ ] **Step 3: Implement one reusable policy predicate**

```python
def scope_filters(context: RequestContext) -> tuple[ColumnElement[bool], ...]:
    filters = [AuditEvent.unit_id == context.unit_id]
    if "unit_auditor" in context.roles:
        return tuple(filters)
    filters.append(AuditEvent.project_id == context.project_id)
    if "project_admin" not in context.roles:
        filters.append(AuditEvent.user_id == context.user_id)
    return tuple(filters)
```

All Repository methods must accept these filters rather than reconstructing role logic.

- [ ] **Step 4: Implement list, summary, detail, and related queries**

Use a filtered subquery, stable `occurred_at DESC, id DESC` pagination, literal escaped ILIKE, and one aggregate query. Related events first require a scoped anchor, then query `trace_id` with the same scope and ascending order.

Pydantic responses:

```python
class AuditEventPage(BaseModel):
    items: list[AuditEventListItem]
    page: int
    page_size: int
    total: int
    summary: AuditSummary

class AuditSummary(BaseModel):
    total: int
    failed: int
    high_risk: int
    runtime: int
    management: int
    by_source: dict[AuditSource, int]
```

- [ ] **Step 5: Implement routes and mount them**

Expose:

```text
GET /api/audit/events
GET /api/audit/events/{event_id}
GET /api/audit/events/{event_id}/related
```

Use Query bounds and Literals for enums, validate timezone/order, inject RequestContext and Session, and translate scoped misses to `404 记录不存在或无权访问`.

- [ ] **Step 6: Run API tests and commit**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path backend)
uv run pytest backend/tests/audit backend/tests/test_main.py -q
```

Expected: PASS.

```bash
git add backend/app/audit backend/app/main.py backend/tests/audit backend/tests/test_main.py
git commit -m "feat: expose scoped audit API"
```

### Task 5: Record Agent, Tool, and LLM Runtime Events

**Files:**

- Modify: `backend/app/conversations/service.py`
- Modify: `backend/app/conversations/repository.py`
- Modify: `backend/app/runtime/harness.py`
- Modify: `backend/app/tools/gateway.py`
- Modify: `backend/tests/conversations/test_service.py`
- Modify: `backend/tests/runtime/test_harness.py`
- Modify: `backend/tests/tools/test_gateway.py`

- [ ] **Step 1: Write failing runtime audit tests**

Assert one Web message produces:

- `agent.run.created` in the same commit as Run creation.
- Running plus terminal Agent status events.
- One LLM success/failure event per model iteration with provider/model, Token, and duration but no prompt content.
- Tool started and succeeded/failed events linked by Trace and parent event.
- Replaying a completion path does not duplicate the terminal audit event.
- Failure to record Agent creation rolls back the message and Run.

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path backend)
uv run pytest backend/tests/conversations/test_service.py backend/tests/runtime/test_harness.py backend/tests/tools/test_gateway.py -q
```

Expected: FAIL because runtime services do not receive an AuditRecorder.

- [ ] **Step 3: Add audit dependencies without hidden globals**

Construct Recorder with the existing SQLAlchemy Session:

```python
audit = AuditRecorder()
audit.record(
    self.repository.session,
    AuditRecordRequest(
        unit_id=context.unit_id,
        project_id=context.project_id,
        user_id=context.user_id,
        category="runtime",
        source="agent",
        action="agent.run.created",
        status="succeeded",
        risk_level="low",
        trace_id=run.id,
        run_id=run.id,
        resource_type="agent",
        resource_id=actor_id,
        idempotency_key=f"agent:{run.id}:created",
        occurred_at=datetime.now(UTC),
        summary="Agent Run 已创建",
        metadata={"actor_type": request.actor_type},
        allowed_metadata_keys=frozenset({"actor_type"}),
    ),
)
```

Add audit recording before the existing commit. Extend execution context with unit/project/user. Measure each `model_gateway.generate` call with `perf_counter`, record its selection, usage, status, duration, iteration, and safe error code. Never include `messages`, system prompts, API headers, or raw model content.

Integrate ToolGateway start/finish records into its existing commit blocks so invocation, RunEvent, and audit event share the same transaction.

- [ ] **Step 4: Run runtime tests and commit**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path backend)
uv run pytest backend/tests/conversations backend/tests/runtime backend/tests/tools -q
```

Expected: PASS.

```bash
git add backend/app/conversations backend/app/runtime backend/app/tools backend/tests/conversations backend/tests/runtime backend/tests/tools
git commit -m "feat: audit agent runtime activity"
```

### Task 6: Backfill Existing Agent Runs Safely

**Files:**

- Create: `backend/app/audit/backfill.py`
- Create: `backend/tests/audit/test_backfill.py`
- Modify: `backend/app/main.py` only if startup wiring is explicitly needed; prefer a command function.

- [ ] **Step 1: Write idempotent backfill tests**

Seed completed and failed historical Runs in different units. Assert one `agent.run_snapshot` per Run, `metadata_json.backfilled is True`, the correct scope and status are retained, trigger content is absent, and running twice creates no duplicates.

- [ ] **Step 2: Run and verify failure**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path backend)
uv run pytest backend/tests/audit/test_backfill.py -q
```

Expected: FAIL because the backfill does not exist.

- [ ] **Step 3: Implement bounded batches**

Expose:

```python
def backfill_agent_run_snapshots(
    session_factory: sessionmaker[Session],
    *,
    batch_size: int = 500,
) -> int:
    ...
```

Read Runs joined to Conversation in stable ID order, use `audit-backfill:agent:{run_id}` as idempotency key, commit each batch, and return the number newly inserted. Do not run automatically during API startup. Document an explicit deployment command so operators control timing.

- [ ] **Step 4: Run tests and commit**

```powershell
$env:PYTHONPATH=(Resolve-Path backend)
uv run pytest backend/tests/audit/test_backfill.py -q
```

Expected: PASS.

```bash
git add backend/app/audit/backfill.py backend/tests/audit/test_backfill.py
git commit -m "feat: backfill agent audit snapshots"
```

### Task 7: Record Transactional Management Operations

**Files:**

- Modify: `backend/app/agents/router.py`, `service.py`, `store.py`
- Modify: `backend/app/tools/router.py`, `service.py`, `store.py`
- Modify: `backend/app/mcp/router.py`, `service.py`, `store.py`
- Modify: `backend/app/model_providers/router.py`, `service.py`, `store.py`
- Modify: corresponding backend tests.

- [ ] **Step 1: Write failing success, rejection, and rollback tests**

For each resource family, cover at least create/update or toggle/delete. Prove:

- The route requires RequestContext.
- The successful business row and audit event are committed together.
- If Recorder raises, the business mutation is absent after rollback.
- Not found, validation, protected default Agent, stale update, and permission denial create a `failed` audit event with a stable error code and no secret input.
- Provider API keys and MCP headers never appear in audit summary/metadata.

- [ ] **Step 2: Run management tests and verify failure**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path backend)
uv run pytest backend/tests/test_agents.py backend/tests/tools/test_api.py backend/tests/test_mcp.py backend/tests/test_model_providers.py -q
```

Expected: FAIL because routes have no context and stores commit independently.

- [ ] **Step 3: Introduce explicit transaction ownership**

Add optional shared-session mutation methods to each store. Shared-session methods flush but never commit; existing read methods remain unchanged. Services accept `context`, `session`, and `recorder` for mutations:

```python
def update(
    self,
    context: RequestContext,
    session: Session,
    agent_id: str,
    request: AgentConfig,
) -> AgentInfo:
    record = self.store.update_in_session(session, agent_id, request.model_dump())
    self.audit.record(session, management_event(...))
    session.commit()
    return self._info(record)
```

Do not wrap filesystem deletion in the database transaction. For Agent workspace deletion, rename the directory to a same-parent quarantine name first, commit DB plus audit, then remove quarantine; on rollback restore the rename. Add focused tests for this compensation path.

- [ ] **Step 4: Record failed attempts separately**

Router/service exception handling records a `failed` event in a fresh short transaction only after the business transaction is rolled back. Use the attempted action/resource ID, safe error code, context scope, request ID, and no request body. Permission middleware records denied requests with source matching the resource module.

- [ ] **Step 5: Run management and full backend tests**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path backend)
uv run pytest backend/tests/test_agents.py backend/tests/tools/test_api.py backend/tests/test_mcp.py backend/tests/test_model_providers.py -q
uv run pytest backend/tests -q
```

Expected: all backend tests PASS; PostgreSQL migration test may show only its documented environment skip.

- [ ] **Step 6: Commit**

```bash
git add backend/app/agents backend/app/tools backend/app/mcp backend/app/model_providers backend/tests
git commit -m "feat: audit management mutations"
```

### Task 8: Add the Frontend Audit API Client

**Files:**

- Create: `frontend/src/api/audit.ts`
- Create: `frontend/src/api/audit.test.ts`

- [ ] **Step 1: Write failing request contract tests**

Verify filters omit empty values, dates and enums serialize exactly, IDs are encoded, and AbortSignal reaches list/detail/related requests.

```ts
await auditApi.list({
  page: 2,
  page_size: 50,
  source: 'tool',
  query: '%_\\',
}, controller.signal);

expect(fetchMock).toHaveBeenCalledWith(
  expect.stringContaining('/audit/events?page=2&page_size=50&source=tool&query=%25_%5C'),
  expect.objectContaining({ signal: controller.signal }),
);
```

- [ ] **Step 2: Run and verify failure**

Run:

```powershell
cd frontend
npm test -- src/api/audit.test.ts
```

Expected: FAIL because `auditApi` does not exist.

- [ ] **Step 3: Implement exact TypeScript contracts**

Define literal unions matching backend enums, `AuditEventListItem`, `AuditEventDetail`, `AuditSummary`, `AuditEventPage`, and methods:

```ts
export const auditApi = {
  list: (filters: AuditFilters, signal?: AbortSignal) =>
    request<AuditEventPage>(`/audit/events?${toParams(filters)}`, { signal }),
  get: (eventId: string, signal?: AbortSignal) =>
    request<AuditEventDetail>(`/audit/events/${encodeURIComponent(eventId)}`, { signal }),
  related: (eventId: string, signal?: AbortSignal) =>
    request<AuditEventListItem[]>(`/audit/events/${encodeURIComponent(eventId)}/related`, { signal }),
};
```

- [ ] **Step 4: Run tests and commit**

```powershell
cd frontend
npm test -- src/api/audit.test.ts
```

Expected: PASS.

```bash
git add frontend/src/api/audit.ts frontend/src/api/audit.test.ts
git commit -m "feat: add unified audit client"
```

### Task 9: Build the Real `/system/audit` Page

**Files:**

- Create: `frontend/src/views/security/AuditLogView.vue`
- Create: `frontend/src/views/security/AuditLogView.test.ts`
- Modify: `frontend/src/router/routes.ts`
- Modify: `frontend/src/views/runs/AgentRunListView.vue`
- Modify: `frontend/src/views/runs/AgentRunListView.test.ts`

- [ ] **Step 1: Write failing page behavior tests**

Cover:

- First page and summary rendering.
- All filters reset page and issue one request.
- Page-size change issues one request.
- Detail and related Timeline load only after opening.
- Close/switch aborts all pending detail requests and old responses cannot overwrite.
- Detail and related errors retry independently.
- Safe 404 text.
- Unknown enums render neutral labels.
- Agent event button routes to `/runs?run_id=...`.
- `/runs?run_id=...` opens that Run drawer after list load.
- Route uses `AuditLogView`, not GenericModuleView.

- [ ] **Step 2: Run and verify failure**

Run:

```powershell
cd frontend
npm test -- src/views/security/AuditLogView.test.ts src/views/runs/AgentRunListView.test.ts
```

Expected: FAIL because the view and deep-link behavior do not exist.

- [ ] **Step 3: Implement the compact audit page**

Use the approved structure: five-item summary strip, dense filter grid, horizontally scrollable stable table, pagination footer, and one right drawer. Use existing color tokens and radius no larger than 8px. Do not change global styles.

State requirements:

```ts
let listGeneration = 0;
let listController: AbortController | undefined;
const detailRequests = {
  event: { generation: 0, controller: undefined as AbortController | undefined },
  related: { generation: 0, controller: undefined as AbortController | undefined },
};
```

Each request checks active event ID plus its generation before writing. List, detail, and related errors preserve successful sibling data. Render metadata with `JSON.stringify` inside `pre`, never `v-html`.

- [ ] **Step 4: Add Agent Run deep linking**

Audit event:

```ts
router.push({ path: '/runs', query: { run_id: event.run_id } });
```

AgentRunListView reads a validated string `route.query.run_id` after the initial list request and calls its existing `openRun` once. Invalid or missing IDs do nothing; a 404 uses the existing safe message.

- [ ] **Step 5: Run page tests, type check, and build**

Run:

```powershell
cd frontend
npm test -- src/views/security/AuditLogView.test.ts src/views/runs/AgentRunListView.test.ts
npx vue-tsc --noEmit
npm run build
```

Expected: tests PASS, type check exits 0, build exits 0; the existing chunk-size warning is acceptable.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/security frontend/src/views/runs frontend/src/router/routes.ts
git commit -m "feat: add unified audit page"
```

### Task 10: Document, Verify, and Browser-Accept the Feature

**Files:**

- Modify: `backend/README.md`
- Modify: `frontend/README.md`
- Modify: `.env.example`
- Modify: `docs/智能体平台详细功能设计与现状改造清单.md`

- [ ] **Step 1: Update implementation documentation**

Document:

- `VITE_DEV_UNIT_ID` and `VITE_DEV_USER_ROLES`.
- Audit API filters, timezone rules, role scope, safe 404, redaction, and append-only behavior.
- Controlled backfill command and its idempotency.
- `/system/audit` is implemented for Agent/tool/LLM and selected management sources.
- Knowledge, real MCP execution, sandbox, export, retention automation, and event bus remain future work.
- Never include credentials, database passwords, customer data, or raw prompts.

- [ ] **Step 2: Run complete automated verification**

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path backend)
uv run pytest backend/tests -q
cd frontend
npm test
npx vue-tsc --noEmit
npm run build
```

Expected: backend and frontend suites PASS, type check/build exit 0, with only documented environment skips or existing chunk warning.

When PostgreSQL test infrastructure is available:

```powershell
$env:TEST_DATABASE_URL=$env:DATABASE_URL
$env:PYTHONPATH=(Resolve-Path backend)
uv run pytest backend/tests/integration/test_postgres_migrations.py -q
```

Expected: PASS and all audit indexes/constraints exist.

- [ ] **Step 3: Run real browser acceptance**

1. Start PostgreSQL, API, and frontend from the feature worktree.
2. Modify a disposable test Agent and verify one management event.
3. In Web chat send: `请调用平台工具确认当前日期、星期和当前项目，只返回核验结果。`
4. Open `/system/audit`; verify Agent, two Tool events, and LLM events share the correct Trace and show no prompt or secret.
5. Open the Agent event, use “查看 Agent Run”, and verify the matching `/runs` drawer.
6. Repeat API queries as unit auditor, project admin, and user; verify scoped counts.
7. Check desktop and mobile screenshots, table overflow, drawer layout, console errors, and request failures.

- [ ] **Step 4: Run final spec and hygiene checks**

Run:

```powershell
rg -n "TBD|TODO|change-me|sk-" backend frontend docs
git diff --check
git status --short
```

Expected: no secrets or unresolved placeholders in changed files, no whitespace errors, and only intended changes.

- [ ] **Step 5: Commit documentation**

```bash
git add .env.example backend/README.md frontend/README.md docs/智能体平台详细功能设计与现状改造清单.md
git commit -m "docs: document unified audit center"
```

## Final Review Gate

Before merging:

1. Request a spec-compliance review against `docs/superpowers/specs/2026-08-03-unified-audit-center-design.md`.
2. Request a code-quality and security review focused on cross-unit leakage, transaction ownership, idempotency, redaction, old-request races, and migration downgrade safety.
3. Fix every Critical or Important finding.
4. Re-run the complete backend suite, frontend suite, type check, production build, PostgreSQL migration test, and browser acceptance.
5. Present merge/push choices; do not merge or push without explicit user approval.
