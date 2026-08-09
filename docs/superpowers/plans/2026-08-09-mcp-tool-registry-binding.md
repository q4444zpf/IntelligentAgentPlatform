# MCP Tool Registry And Agent Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Register synchronized HTTP/SSE MCP tools in the unified tool registry, require administrator publication, and allow Agents to bind only currently available published tools.

**Architecture:** Extend `registered_tools` with explicit source mapping and source availability, then introduce a focused MCP-to-registry synchronizer used inside existing MCP transactions. The tool registry remains the only Agent-facing capability catalogue; MCP credentials stay in `mcp_clients`, and ToolGateway execution remains unchanged in this phase.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16, pytest, Vue 3, TypeScript, Ant Design Vue, Vitest.

## Global Constraints

- MCP tools are registered with `published=false` and require unit-administrator review before Agent binding.
- `enabled` is administrator-controlled; `source_available` is controlled by MCP lifecycle synchronization.
- Agent configuration stores only unified `tool_id` values and never stores MCP URLs, headers, environment variables, commands, or credentials.
- HTTP/SSE `tools/call` and stdio execution are out of scope.
- MCP synchronization, lifecycle changes, tool registry changes, and management audit writes must share one database transaction.
- Before applying the schema migration to the configured PostgreSQL database, create a timestamped local `pg_dump` backup.
- Do not commit database credentials, MCP credentials, backup archives, runtime logs, or temporary MCP services.
- Use TDD for every behavior change: failing test, observed failure, minimal implementation, passing test.

---

## File Structure

- Create `backend/alembic/versions/20260809_13_mcp_tool_registry_binding.py`: add and remove registry source columns.
- Create `backend/app/mcp/tool_registry.py`: stable MCP tool ID generation and transactional registry synchronization.
- Create `backend/tests/mcp/test_tool_registry_sync.py`: MCP registry identity, sync, lifecycle, rollback, and credential-boundary tests.
- Modify `backend/app/db/platform_models.py`: persist MCP source mapping and source availability.
- Modify `backend/app/tools/schemas.py`: expose safe source metadata and publication request schema.
- Modify `backend/app/tools/store.py`: transactional MCP upsert, source availability updates, and publication updates.
- Modify `backend/app/tools/service.py`: bindability and publication policy.
- Modify `backend/app/tools/router.py`: publication endpoint and management audit handling.
- Modify `backend/app/mcp/service.py`: invoke the synchronizer within current transactions.
- Modify `backend/tests/tools/test_registry.py`: publication and bindability behavior.
- Modify `backend/tests/test_mcp.py`: route-level lifecycle integration.
- Modify `backend/tests/test_agents.py`: MCP tool binding acceptance and rejection.
- Modify `backend/tests/integration/test_postgres_migrations.py`: migration column and downgrade coverage.
- Modify `.gitignore`: ignore the local database backup directory.
- Modify `frontend/src/api/tools.ts`: source fields and publication API.
- Modify `frontend/src/views/tools/ToolManageView.vue`: source availability and publish/unpublish controls.
- Modify `frontend/src/views/tools/ToolManageView.test.ts`: registry publication interactions.
- Modify `frontend/src/views/agent/AgentManageView.vue`: include `source_available` in selection and stale-binding messaging.
- Modify `frontend/src/views/agent/AgentManageView.test.ts`: MCP source-unavailable binding behavior.

---

### Task 1: Database Backup Contract And Registry Source Columns

**Files:**
- Modify: `.gitignore`
- Create: `backend/alembic/versions/20260809_13_mcp_tool_registry_binding.py`
- Modify: `backend/app/db/platform_models.py`
- Modify: `backend/app/tools/schemas.py`
- Modify: `backend/app/tools/store.py`
- Test: `backend/tests/tools/test_registry.py`
- Test: `backend/tests/integration/test_postgres_migrations.py`

**Interfaces:**
- Produces `ToolInfo.source_resource_id: str | None`, `source_capability_id: str | None`, and `source_available: bool`.
- Preserves existing builtin initialization and administrator `enabled` state.

- [ ] **Step 1: Add the failing registry contract test**

Add to `backend/tests/tools/test_registry.py`:

```python
def test_builtin_tools_expose_empty_source_mapping_and_available_source(tool_store):
    store, _ = tool_store
    tool = ToolService(store).get("system.get_current_time")
    assert tool.source_resource_id is None
    assert tool.source_capability_id is None
    assert tool.source_available is True
```

- [ ] **Step 2: Run the test and observe the missing fields**

Run:

```powershell
$env:PYTHONPATH='backend'
pytest backend/tests/tools/test_registry.py::test_builtin_tools_expose_empty_source_mapping_and_available_source -q
```

Expected: FAIL because `ToolInfo` does not expose the three fields.

- [ ] **Step 3: Add model, schema, decode, and builtin defaults**

Add to `RegisteredToolRecord`:

```python
source_resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
source_capability_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
source_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
```

Add matching fields to `ToolInfo`, return them from `ToolStore._decode()`, and ensure builtin upsert does not overwrite `enabled` while returning `source_available=True`.

- [ ] **Step 4: Add the migration and migration assertions**

Create revision `20260809_13`, down revision `20260808_12`:

```python
def upgrade() -> None:
    op.add_column("registered_tools", sa.Column("source_resource_id", sa.String(128), nullable=True))
    op.add_column("registered_tools", sa.Column("source_capability_id", sa.String(256), nullable=True))
    op.add_column(
        "registered_tools",
        sa.Column("source_available", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("registered_tools", "source_available")
    op.drop_column("registered_tools", "source_capability_id")
    op.drop_column("registered_tools", "source_resource_id")
```

Update the PostgreSQL migration test to assert the three columns and current head `20260809_13`.

- [ ] **Step 5: Ignore and create the local backup directory**

Add to `.gitignore`:

```gitignore
/.local-backups/
```

Create the backup before applying the migration:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
New-Item -ItemType Directory -Force .local-backups | Out-Null
$backupName = "iap-$stamp.dump"
docker compose -f compose.yaml exec -T postgres pg_dump -U iap -d iap -Fc -f "/tmp/$backupName"
docker compose -f compose.yaml cp "postgres:/tmp/$backupName" ".local-backups/$backupName"
Get-Item ".local-backups/iap-$stamp.dump" | Select-Object FullName,Length
```

Expected: the dump exists and has a non-zero length.

- [ ] **Step 6: Run unit tests and migration cycle**

Run:

```powershell
$env:PYTHONPATH='backend'
pytest backend/tests/tools/test_registry.py -q
docker compose -f compose.yaml exec -T postgres dropdb -U iap --if-exists iap_mcp_registry_test
docker compose -f compose.yaml exec -T postgres createdb -U iap iap_mcp_registry_test
$env:TEST_DATABASE_URL='postgresql+psycopg://iap:iap@127.0.0.1:5432/iap_mcp_registry_test'
pytest backend/tests/integration/test_postgres_migrations.py -q
docker compose -f compose.yaml exec -T postgres dropdb -U iap --if-exists iap_mcp_registry_test
```

Expected: PASS, with migration upgrade and downgrade restoring the database to head.

- [ ] **Step 7: Commit the schema contract**

```powershell
git add .gitignore backend/alembic/versions/20260809_13_mcp_tool_registry_binding.py backend/app/db/platform_models.py backend/app/tools/schemas.py backend/app/tools/store.py backend/tests/tools/test_registry.py backend/tests/integration/test_postgres_migrations.py
git commit -m "feat: 扩展MCP工具来源字段"
```

---

### Task 2: Stable MCP Tool Identity And Transactional Registration

**Files:**
- Create: `backend/app/mcp/tool_registry.py`
- Create: `backend/tests/mcp/test_tool_registry_sync.py`
- Modify: `backend/app/tools/store.py`
- Modify: `backend/app/mcp/service.py`

**Interfaces:**
- Produces `build_mcp_tool_id(client_key: str, remote_tool_name: str) -> str`.
- Produces `McpToolRegistrySynchronizer.sync(session, client_record, remote_tools) -> list[str]`.
- Consumes one existing SQLAlchemy `Session`; it never commits independently.

- [ ] **Step 1: Write failing identity tests**

```python
def test_build_mcp_tool_id_is_stable_valid_and_bounded():
    first = build_mcp_tool_id("water-data", "查询 水位/实时值")
    second = build_mcp_tool_id("water-data", "查询 水位/实时值")
    assert first == second
    assert first.startswith("mcp.water_data.")
    assert len(first) <= 128
    assert TOOL_ID_PATTERN.fullmatch(first)


def test_different_remote_names_do_not_collide():
    assert build_mcp_tool_id("water", "a-b") != build_mcp_tool_id("water", "a b")
```

- [ ] **Step 2: Run identity tests and observe import failure**

Run:

```powershell
$env:PYTHONPATH='backend'
pytest backend/tests/mcp/test_tool_registry_sync.py -q -k 'tool_id'
```

Expected: FAIL because `backend/app/mcp/tool_registry.py` does not exist.

- [ ] **Step 3: Implement stable identity generation**

Implement ASCII slug normalization plus an eight-character SHA-256 suffix. Allocate lengths so the full ID is never longer than 128 characters and every segment satisfies `TOOL_ID_PATTERN`.

```python
def build_mcp_tool_id(client_key: str, remote_tool_name: str) -> str:
    digest = hashlib.sha256(f"{client_key}\0{remote_tool_name}".encode()).hexdigest()[:8]
    client_slug = _slug(client_key, fallback="client")[:40]
    tool_slug = _slug(remote_tool_name, fallback="tool")[:60]
    return f"mcp.{client_slug}.{tool_slug}_{digest}"
```

- [ ] **Step 4: Write the failing sync registration test**

Create this focused fixture in `backend/tests/mcp/test_tool_registry_sync.py`:

```python
@pytest.fixture
def registry_service(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'mcp-registry.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "tools-sync", "result": {"tools": [
            {"name": "query_reservoir_level", "description": "查询水位", "inputSchema": {"type": "object"}},
            {"name": "dispatch_gate", "description": "调度闸门", "inputSchema": {"type": "object"}},
        ]}})

    service = McpService(
        McpStore(factory),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        tool_store=ToolStore(factory),
    )
    return service, factory
```

Then add:

```python
def test_sync_registers_mcp_tools_as_available_unpublished(registry_service):
    service, factory = registry_service
    context = RequestContext(unit_id="unit-1", project_id="project-1", user_id="admin", roles=frozenset({"unit_admin"}))
    request = McpClientCreate.model_validate({
        "key": "water-data",
        "name": "水情 MCP",
        "transport": "streamable_http",
        "url": "https://water.example.com/mcp",
        "headers": {},
        "enabled": True,
    })
    with factory() as session:
        service.create(request, context=context, session=session, request_id="create-water")
    with factory() as session:
        service.sync_tools("water-data", context=context, session=session, request_id="sync-water")
    tools = [item for item in ToolStore(factory).list() if item["source"] == "mcp"]
    assert len(tools) == 2
    assert all(item["published"] is False for item in tools)
    assert all(item["enabled"] is True for item in tools)
    assert all(item["source_available"] is True for item in tools)
    assert {item["source_resource_id"] for item in tools} == {"water-data"}
```

- [ ] **Step 5: Run the sync test and observe zero registered MCP tools**

Run:

```powershell
$env:PYTHONPATH='backend'
pytest backend/tests/mcp/test_tool_registry_sync.py::test_sync_registers_mcp_tools_as_available_unpublished -q
```

Expected: FAIL because synchronization only updates `mcp_clients.tool_records`.

- [ ] **Step 6: Implement transactional MCP upsert**

Add `ToolStore.upsert_mcp_in_session()` using dialect-specific `INSERT ... ON CONFLICT DO UPDATE`. On conflict update contract fields and source mapping, but do not overwrite `risk_level`, `requires_approval`, `published`, or `enabled`.

The initial record must contain:

```python
{
    "tool_id": tool_id,
    "version": "1.0.0",
    "name": remote_tool["name"],
    "description": remote_tool.get("description", ""),
    "source": "mcp",
    "risk_level": "medium",
    "input_schema": remote_tool.get("inputSchema", {"type": "object"}),
    "output_schema": {"type": "object", "additionalProperties": True},
    "requires_approval": False,
    "published": False,
    "enabled": True,
    "source_resource_id": client_key,
    "source_capability_id": remote_tool["name"],
    "source_available": True,
}
```

Inject `ToolStore` into `McpService` and call the synchronizer after the MCP CAS update and before management audit commit.

- [ ] **Step 7: Verify registration and rollback**

Add a recorder-failure test asserting both `mcp_clients.tool_records` and MCP registry rows roll back. Run:

```powershell
$env:PYTHONPATH='backend'
pytest backend/tests/mcp/test_tool_registry_sync.py backend/tests/test_mcp.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit MCP registration**

```powershell
git add backend/app/mcp/tool_registry.py backend/app/mcp/service.py backend/app/tools/store.py backend/tests/mcp/test_tool_registry_sync.py backend/tests/test_mcp.py
git commit -m "feat: 同步MCP工具到统一目录"
```

---

### Task 3: MCP Lifecycle Propagation

**Files:**
- Modify: `backend/app/mcp/tool_registry.py`
- Modify: `backend/app/mcp/service.py`
- Modify: `backend/app/tools/store.py`
- Test: `backend/tests/mcp/test_tool_registry_sync.py`

**Interfaces:**
- Produces `McpToolRegistrySynchronizer.apply_client_state(session, client_record) -> None`.
- Produces `McpToolRegistrySynchronizer.retire_client(session, client_key) -> None`.

- [ ] **Step 1: Write failing lifecycle tests**

Cover these exact transitions:

```python
def test_client_disable_marks_source_unavailable_and_unpublishes(client):
    assert client.post("/api/mcp", json=remote_payload()).status_code == 201
    assert client.post("/api/mcp/water-data/tools/sync").status_code == 200
    service = client.app.state.mcp_service
    tool_id = build_mcp_tool_id("water-data", "query_reservoir_level")
    with service.store.session_factory.begin() as session:
        session.get(RegisteredToolRecord, tool_id).published = True

    assert client.patch("/api/mcp/water-data/toggle").status_code == 200
    tool = service.tool_store.get(tool_id)
    assert tool["source_available"] is False
    assert tool["published"] is False
    assert tool["enabled"] is True


def test_client_delete_retires_registry_rows_without_deleting_them(client):
    assert client.post("/api/mcp", json=remote_payload()).status_code == 201
    assert client.post("/api/mcp/water-data/tools/sync").status_code == 200
    service = client.app.state.mcp_service
    tool_id = build_mcp_tool_id("water-data", "query_reservoir_level")
    assert client.delete("/api/mcp/water-data").status_code == 200
    tool = service.tool_store.get(tool_id)
    assert tool is not None
    assert tool["source_available"] is False
    assert tool["published"] is False
```

Also add whitelist-removal, missing-remote-tool, and reappearing-tool cases using the same real store. The whitelist test must assert the retained tool stays available while the removed tool becomes unavailable. The missing-tool test must replace the mock `tools/list` response with one tool and assert the absent row is retained. The reappearance test must restore the two-tool response and assert `source_available=True` while `published=False` remains unchanged.

Use these concrete assertions:

```python
def test_whitelist_removal_marks_only_removed_tool_unavailable(client):
    assert client.post("/api/mcp", json=remote_payload()).status_code == 201
    assert client.post("/api/mcp/water-data/tools/sync").status_code == 200
    assert client.put(
        "/api/mcp/water-data/tools",
        json={"tools": ["query_reservoir_level"]},
    ).status_code == 200
    service = client.app.state.mcp_service
    query = service.tool_store.get(build_mcp_tool_id("water-data", "query_reservoir_level"))
    dispatch = service.tool_store.get(build_mcp_tool_id("water-data", "dispatch_gate"))
    assert query["source_available"] is True
    assert dispatch["source_available"] is False
    assert dispatch["published"] is False


def test_missing_and_reappearing_remote_tool_requires_republication(client):
    assert client.post("/api/mcp", json=remote_payload()).status_code == 201
    assert client.post("/api/mcp/water-data/tools/sync").status_code == 200
    service = client.app.state.mcp_service
    dispatch_id = build_mcp_tool_id("water-data", "dispatch_gate")
    with service.store.session_factory.begin() as session:
        session.get(RegisteredToolRecord, dispatch_id).published = True

    service.http_client = httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(
        200,
        json={"result": {"tools": [{"name": "query_reservoir_level", "inputSchema": {"type": "object"}}]}},
    )))
    assert client.post("/api/mcp/water-data/tools/sync").status_code == 200
    assert service.tool_store.get(dispatch_id)["source_available"] is False

    service.http_client = httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(
        200,
        json={"result": {"tools": [
            {"name": "query_reservoir_level", "inputSchema": {"type": "object"}},
            {"name": "dispatch_gate", "inputSchema": {"type": "object"}},
        ]}},
    )))
    assert client.post("/api/mcp/water-data/tools/sync").status_code == 200
    restored = service.tool_store.get(dispatch_id)
    assert restored["source_available"] is True
    assert restored["published"] is False
```

- [ ] **Step 2: Run lifecycle tests and observe stale registry state**

```powershell
$env:PYTHONPATH='backend'
pytest backend/tests/mcp/test_tool_registry_sync.py -q -k 'disable or whitelist or missing or reappearing or delete'
```

Expected: FAIL because MCP lifecycle operations do not update the registry.

- [ ] **Step 3: Implement source availability updates**

Add `ToolStore.update_mcp_source_state_in_session(session, client_key, available_tool_names, client_enabled)` that:

- selects registry rows where `source='mcp'` and `source_resource_id=client_key`;
- sets `source_available` from client state, whitelist, and current tool snapshot;
- sets `published=False` whenever availability becomes false;
- never changes administrator `enabled`;
- flushes without committing.

Call it from MCP toggle, whitelist update, sync, and delete in the same transaction.

- [ ] **Step 4: Verify lifecycle and concurrency regression**

```powershell
$env:PYTHONPATH='backend'
pytest backend/tests/mcp/test_tool_registry_sync.py backend/tests/test_mcp.py -q
```

Expected: PASS including existing CAS conflict tests.

- [ ] **Step 5: Commit lifecycle propagation**

```powershell
git add backend/app/mcp/tool_registry.py backend/app/mcp/service.py backend/app/tools/store.py backend/tests/mcp/test_tool_registry_sync.py
git commit -m "feat: 同步MCP工具生命周期"
```

---

### Task 4: Administrator Publication And Agent Bindability

**Files:**
- Modify: `backend/app/tools/schemas.py`
- Modify: `backend/app/tools/store.py`
- Modify: `backend/app/tools/service.py`
- Modify: `backend/app/tools/router.py`
- Modify: `backend/tests/tools/test_registry.py`
- Modify: `backend/tests/test_agents.py`

**Interfaces:**
- Produces `ToolPublicationRequest(published: bool)`.
- Produces `ToolService.set_published(tool_id, published, context, session, request_id) -> ToolInfo`.
- Produces `PATCH /api/tools/{tool_id}/publication`.

- [ ] **Step 1: Write failing publication policy tests**

```python
def test_admin_can_publish_available_mcp_tool(tool_store):
    service, context, tool_id = seeded_mcp_tool(tool_store, source_available=True)
    with tool_store[1]() as session:
        result = service.set_published(tool_id, True, context=context, session=session)
    assert result.published is True


def test_cannot_publish_source_unavailable_mcp_tool(tool_store):
    service, context, tool_id = seeded_mcp_tool(tool_store, source_available=False)
    with tool_store[1]() as session:
        with pytest.raises(ToolValidationError, match="source is unavailable"):
            service.set_published(tool_id, True, context=context, session=session)
```

- [ ] **Step 2: Run publication tests and observe missing API**

```powershell
$env:PYTHONPATH='backend'
pytest backend/tests/tools/test_registry.py -q -k 'publish'
```

Expected: FAIL because `set_published` is not implemented.

- [ ] **Step 3: Implement publication and audit**

Add a transactional store update, reject publication when `source_available=False`, and record `resource.published` or `resource.unpublished` with source metadata only.

Add route:

```python
@router.patch("/{tool_id}/publication", response_model=ToolInfo)
def set_tool_publication(
    tool_id: str,
    request: ToolPublicationRequest,
    context: RequestContext = Depends(require_tool_admin),
    request_id: str = Depends(management_request_id),
):
    service_instance = manager()
    with service_instance.store.session_factory() as session:
        return call_management(
            lambda: service_instance.set_published(
                tool_id,
                request.published,
                context=context,
                session=session,
                request_id=request_id,
            ),
            session,
            context,
            request_id,
            tool_id,
        )
```

- [ ] **Step 4: Make bindability require source availability**

Change `ToolService.resolve_bindable()` and `ToolStore.get_executable()` to require:

```python
tool["published"] and tool["enabled"] and tool["source_available"]
```

Add Agent API tests showing an available published MCP tool is accepted and an unavailable MCP tool returns 422.

- [ ] **Step 5: Run tool and Agent tests**

```powershell
$env:PYTHONPATH='backend'
pytest backend/tests/tools/test_registry.py backend/tests/test_agents.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit publication and binding policy**

```powershell
git add backend/app/tools/schemas.py backend/app/tools/store.py backend/app/tools/service.py backend/app/tools/router.py backend/tests/tools/test_registry.py backend/tests/test_agents.py
git commit -m "feat: 增加MCP工具审核发布"
```

---

### Task 5: Tool Registry Publication UI

**Files:**
- Modify: `frontend/src/api/tools.ts`
- Modify: `frontend/src/views/tools/ToolManageView.vue`
- Modify: `frontend/src/views/tools/ToolManageView.test.ts`

**Interfaces:**
- Consumes `PATCH /tools/{toolId}/publication` with `{ published: boolean }`.
- Exposes safe MCP source metadata and `source_available` to the registry view.

- [ ] **Step 1: Write failing UI interaction tests**

Add tests that return an MCP tool and assert:

```typescript
expect(wrapper.text()).toContain('water-data')
expect(wrapper.text()).toContain('query_level')
expect(wrapper.text()).toContain('来源不可用')
expect(wrapper.get('[aria-label="发布工具"]').attributes('disabled')).toBeDefined()
```

Add a second available-tool test that clicks `发布工具` and expects:

```typescript
expect(mocks.publish).toHaveBeenCalledWith('mcp.water.query_level_abcd1234', true)
```

- [ ] **Step 2: Run the UI tests and observe missing publication controls**

```powershell
npm test -- ToolManageView.test.ts
```

Expected: FAIL because the API and buttons do not exist.

- [ ] **Step 3: Extend the TypeScript contract and API**

Add:

```typescript
source_resource_id: string | null;
source_capability_id: string | null;
source_available: boolean;
```

and:

```typescript
setPublished: (toolId: string, published: boolean) =>
  request<ToolInfo>(`/tools/${encodeURIComponent(toolId)}/publication`, {
    method: 'PATCH',
    ...json({ published }),
  }),
```

- [ ] **Step 4: Add publication controls and status rendering**

For MCP tools show client ID and remote tool name, render `来源不可用` when false, disable publication in that state, and keep publication loading state separate from enable/disable loading state.

- [ ] **Step 5: Run focused and full frontend tests**

```powershell
npm test -- ToolManageView.test.ts AgentManageView.test.ts
npm test
```

Expected: PASS.

- [ ] **Step 6: Commit registry UI**

```powershell
git add frontend/src/api/tools.ts frontend/src/views/tools/ToolManageView.vue frontend/src/views/tools/ToolManageView.test.ts
git commit -m "feat: 增加MCP工具发布界面"
```

---

### Task 6: Agent UI Source Availability

**Files:**
- Modify: `frontend/src/views/agent/AgentManageView.vue`
- Modify: `frontend/src/views/agent/AgentManageView.test.ts`

**Interfaces:**
- Consumes `ToolInfo.source_available` from Task 5.
- Keeps existing stale-binding removal workflow.

- [ ] **Step 1: Write the failing Agent picker test**

Return a tool with `published=true`, `enabled=true`, and `source_available=false`. Assert it remains visible only when already bound, shows `MCP 来源不可用`, cannot be newly selected, blocks save, and can be removed.

- [ ] **Step 2: Run the test and observe incorrect selectability**

```powershell
npm test -- AgentManageView.test.ts
```

Expected: FAIL because selection currently checks only `published && enabled`.

- [ ] **Step 3: Update Agent selection rules**

Use:

```typescript
function isToolSelectable(tool: ToolInfo) {
  return tool.published && tool.enabled && tool.source_available;
}
```

Render a distinct source-unavailable message while retaining the existing remove-binding action.

- [ ] **Step 4: Run Agent and full frontend tests**

```powershell
npm test -- AgentManageView.test.ts
npm test
```

Expected: PASS, currently 119 tests plus the new cases.

- [ ] **Step 5: Commit Agent UI behavior**

```powershell
git add frontend/src/views/agent/AgentManageView.vue frontend/src/views/agent/AgentManageView.test.ts
git commit -m "feat: 限制Agent绑定不可用MCP工具"
```

---

### Task 7: Integrated PostgreSQL And Browser Acceptance

**Files:**
- Modify only if a verified defect is found in files from Tasks 1-6.
- Test: `backend/tests/test_mcp.py`
- Test: `backend/tests/tools/test_registry.py`
- Test: `backend/tests/test_agents.py`
- Test: `backend/tests/persistence/test_postgres_stores.py`

**Interfaces:**
- Verifies the complete registration and binding control plane without invoking MCP `tools/call`.

- [ ] **Step 1: Run backend regression suites**

```powershell
$env:PYTHONPATH='backend'
pytest backend/tests/test_mcp.py backend/tests/mcp/test_tool_registry_sync.py backend/tests/tools/test_registry.py backend/tests/test_agents.py backend/tests/persistence/test_postgres_stores.py -q
```

Expected: PASS.

- [ ] **Step 2: Apply migration and rebuild services**

```powershell
docker compose -f compose.yaml build api web
docker compose -f compose.yaml up -d postgres api web
docker compose -f compose.yaml ps
```

Expected: PostgreSQL and API healthy; Web running.

- [ ] **Step 3: Run controlled MCP acceptance**

Start a temporary read-only Streamable HTTP MCP fixture outside the repository, then verify through the UI:

1. create `local-test`;
2. synchronize two tools;
3. confirm both appear in Tool Registry as MCP, available, and unpublished;
4. confirm Agent cannot select either tool;
5. publish one tool;
6. confirm Agent can bind only the published tool;
7. disable the MCP client;
8. confirm the tool becomes source-unavailable and unpublished and Agent save requires removing the binding;
9. delete the test client.

- [ ] **Step 4: Verify PostgreSQL cleanup and credential boundaries**

```powershell
docker compose -f compose.yaml exec -T postgres psql -U iap -d iap -tAc "select count(*) from mcp_clients where client_key='local-test';"
docker compose -f compose.yaml exec -T postgres psql -U iap -d iap -tAc "select count(*) from registered_tools where source='mcp' and source_resource_id='local-test' and source_available=true;"
```

Expected: both return `0`. Inspect API logs and confirm no Authorization header, env value, or test secret appears.

- [ ] **Step 5: Run final verification**

```powershell
$env:PYTHONPATH='backend'
pytest backend/tests/identity backend/tests/runtime backend/tests/tools backend/tests/test_mcp.py backend/tests/test_agents.py -q
Set-Location frontend
npm test
npm run build
Set-Location ..
git diff --check
git status --short
```

Expected: all tests and build pass; only intended tracked files are modified; temporary logs and backup archives remain untracked/ignored.

- [ ] **Step 6: Confirm no acceptance-only code remains uncommitted**

Run `git status --short`. If acceptance exposed a defect, return to the task that owns that behavior, add a failing regression test, implement the minimum fix, rerun that task's verification, and use that task's explicit commit command. Do not create an untested catch-all acceptance commit.
