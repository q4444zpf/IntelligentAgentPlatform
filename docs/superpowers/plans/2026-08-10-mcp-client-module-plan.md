# MCP 客户端模块完善 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完善 MCP 客户端模块，使 HTTP/SSE MCP 支持连接测试、标准握手、自动工具同步、健康监控、单位/项目隔离、凭据引用、归档和 Agent 安全调用。

**Architecture:** 在现有 FastAPI/Vue 模块内拆分协议传输、发现同步、健康任务、凭据解析和配置权限服务。HTTP/SSE 网络请求与数据库事务分离；数据库租约保证多 API 实例不会重复健康检测。统一工具注册中心继续负责发布审核和 Agent 可执行条件。

**Tech Stack:** FastAPI、SQLAlchemy、Alembic、PostgreSQL、httpx、Pydantic、Vue 3、Pinia、Ant Design Vue、Vitest、pytest。

## Global Constraints

- 首期完整支持 Streamable HTTP 和 SSE；stdio 只能登记并显示等待沙箱 Worker。
- 新增 MCP 保存后自动启动测试与工具同步；失败保留配置。
- 启用 HTTP/SSE 每 5 分钟健康检测一次，同时提供手动检测。
- 新增工具默认未发布；Schema/描述变化待复核并取消发布；删除工具来源不可用并取消发布。
- MCP 按单位归属、项目授权；后端每次重新校验单位、项目、角色、启用、发布和来源可用状态。
- MCP 配置只保存凭据引用；密钥不进入 API 响应、日志、审计、Agent 配置或工具 Schema。
- 删除采用归档，保留历史绑定、运行记录和审计记录。
- 每次数据库表结构变更前生成 `.local-backups` 本地 PostgreSQL 备份。
- 所有行为变更先写失败测试，再写最小实现，再运行完整回归。
- 不修改主工作树中的无关日志、测试 harness 或 `shadow-mvp-agent-runtime`。

## File Map

- Create: `backend/alembic/versions/20260810_14_mcp_client_module.py` - MCP 单位、项目授权、工具、健康和任务迁移。
- Create: `backend/app/mcp/protocol.py` - JSON-RPC 请求、initialize、initialized、tools/list 分页和响应解析。
- Create: `backend/app/mcp/transports/streamable_http.py` - Streamable HTTP 会话适配器。
- Create: `backend/app/mcp/transports/sse.py` - SSE 事件流和消息端点适配器。
- Create: `backend/app/mcp/discovery_service.py` - 工具规范化、哈希、差异和注册中心同步。
- Create: `backend/app/mcp/health_service.py` - 手动检测、状态转换、失败退避和健康记录。
- Create: `backend/app/mcp/credential_resolver.py` - 凭据引用解析和临时 Header 注入。
- Modify: `backend/app/db/platform_models.py` - 新增 MCP 客户端、工具、项目授权、健康记录和操作模型。
- Modify: `backend/app/mcp/service.py` - 组合配置、协议、发现和状态服务。
- Modify: `backend/app/mcp/router.py` - 测试、任务、健康、项目授权、归档 API。
- Modify: `backend/app/main.py` - 启动/关闭健康调度器。
- Create: `backend/tests/mcp/test_protocol.py` - 协议握手和分页红测。
- Create: `backend/tests/mcp/test_discovery_service.py` - 工具差异和注册中心同步红测。
- Create: `backend/tests/mcp/test_health_service.py` - 健康状态和租约红测。
- Create: `backend/tests/mcp/test_credentials.py` - 凭据脱敏和引用红测。
- Modify: `backend/tests/test_mcp.py` - API 回归和权限隔离。
- Modify: `frontend/src/api/mcp.ts` - 新 API 类型和请求。
- Modify: `frontend/src/views/mcp/McpManageView.vue` - 测试连接、健康、差异、项目授权和归档 UI。
- Modify: `frontend/src/App.vue` - 活动感知会话续期。
- Create: `frontend/src/views/mcp/McpManageView.test.ts` - 页面交互回归。
- Create: `frontend/src/App.test.ts` - 会话续期和失效跳转回归。

### Task 1: Database schema and backup

**Files:** `backend/alembic/versions/20260810_14_mcp_client_module.py`, `backend/app/db/platform_models.py`, `backend/tests/integration/test_mcp_client_migration.py`; `.local-backups/iap-before-mcp-client-module-<timestamp>.dump`.

- [ ] **Step 1: Back up local PostgreSQL before schema changes**

```powershell
New-Item -ItemType Directory -Force .local-backups | Out-Null
docker exec intelligent-agent-platform-postgres-1 pg_dump -U iap -d iap -Fc > .local-backups\iap-before-mcp-client-module-$(Get-Date -Format yyyyMMdd-HHmmss).dump
```

- [ ] **Step 2: Write failing migration tests**

Assert `mcp_clients` has `unit_id`, `status`, health fields and credential reference; assert project grants and health/operation tables exist; assert unique `(unit_id, client_key)`.

- [ ] **Step 3: Run the migration tests and verify they fail**

```powershell
backend\.pydeps\Scripts\pytest.exe backend/tests/integration/test_mcp_client_migration.py -q
```

Expected: FAIL because the new columns/tables do not exist.

- [ ] **Step 4: Implement the Alembic migration and SQLAlchemy models**

Preserve existing client data by assigning the bootstrap unit where available, set status `active`, and keep existing tool records. Add indexes for `(unit_id, status)`, `(client_id, last_checked_at)`, and source availability.

- [ ] **Step 5: Run migration tests and full migration regression**

```powershell
backend\.pydeps\Scripts\pytest.exe backend/tests/integration/test_mcp_client_migration.py backend/tests/integration/test_identity_migrations.py -q
```

- [ ] **Step 6: Commit**

```powershell
git add backend/alembic backend/app backend/tests/integration
git commit -m "feat: 扩展MCP客户端数据模型"
```

### Task 2: Protocol and transport adapters

**Files:** `backend/app/mcp/protocol.py`, `backend/app/mcp/transports/*.py`, `backend/tests/mcp/test_protocol.py`.

- [ ] **Step 1: Write failing tests for initialize and paginated tools/list**

Cover JSON Streamable HTTP, event-stream responses, `Mcp-Session-Id`, SSE endpoint discovery, JSON-RPC ID matching, `nextCursor`, malformed responses, timeout and HTTP error mapping.

- [ ] **Step 2: Run protocol tests and verify expected failures**

```powershell
backend\.pydeps\Scripts\pytest.exe backend/tests/mcp/test_protocol.py -q
```

- [ ] **Step 3: Implement the minimal protocol interfaces**

Expose `McpConnectionResult`, `McpToolDefinition`, and `McpTransportAdapter.connect_and_list_tools(config, credentials)`. Use bounded httpx timeouts and never include request headers in exceptions.

- [ ] **Step 4: Run protocol tests until green**

```powershell
backend\.pydeps\Scripts\pytest.exe backend/tests/mcp/test_protocol.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add backend/app/mcp backend/tests/mcp/test_protocol.py
git commit -m "feat: 增加MCP标准协议握手"
```

### Task 3: Credentials and discovery synchronization

**Files:** `backend/app/mcp/credential_resolver.py`, `backend/app/mcp/discovery_service.py`, `backend/tests/mcp/test_credentials.py`, `backend/tests/mcp/test_discovery_service.py`.

- [ ] **Step 1: Write failing tests**

Assert credential IDs resolve only within the current unit; API responses and raised errors contain no secret; added/changed/removed/unchanged tools produce the specified states and hashes; removed tools are unpublished and unavailable.

- [ ] **Step 2: Run tests and verify red**

```powershell
backend\.pydeps\Scripts\pytest.exe backend/tests/mcp/test_credentials.py backend/tests/mcp/test_discovery_service.py -q
```

- [ ] **Step 3: Implement credential reference resolution and discovery diff**

Normalize missing `inputSchema` to `{"type":"object"}`. Apply all directory updates in one transaction after network work completes. Call existing tool registry sync/apply-client-state methods.

- [ ] **Step 4: Run tests and verify green**

```powershell
backend\.pydeps\Scripts\pytest.exe backend/tests/mcp/test_credentials.py backend/tests/mcp/test_discovery_service.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add backend/app/mcp backend/tests/mcp
git commit -m "feat: 同步MCP工具并保护凭据引用"
```

### Task 4: Health service and asynchronous operations

**Files:** `backend/app/mcp/health_service.py`, `backend/app/mcp/scheduler.py`, `backend/app/main.py`, `backend/tests/mcp/test_health_service.py`.

- [ ] **Step 1: Write failing tests**

Cover state transitions, five-minute eligibility, consecutive failure threshold, exponential backoff, database lease exclusivity, manual test priority and operation status persistence.

- [ ] **Step 2: Run tests and verify red**

```powershell
backend\.pydeps\Scripts\pytest.exe backend/tests/mcp/test_health_service.py -q
```

- [ ] **Step 3: Implement health state machine and scheduler**

Use an atomic lease update before network work, release it in `finally`, write sanitized health history, and mark source tools unavailable/unpublished on offline or authentication failure. Start one scheduler task in application lifespan and cancel it cleanly on shutdown.

- [ ] **Step 4: Run tests and verify green**

```powershell
backend\.pydeps\Scripts\pytest.exe backend/tests/mcp/test_health_service.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add backend/app/mcp backend/app/main.py backend/tests/mcp/test_health_service.py
git commit -m "feat: 增加MCP健康检测和同步任务"
```

### Task 5: MCP API and unit/project authorization

**Files:** `backend/app/mcp/service.py`, `backend/app/mcp/router.py`, `backend/tests/test_mcp.py`.

- [ ] **Step 1: Write failing API tests**

Cover draft test, create-triggered operation, operation polling, manual test, sync, health, project grants, archive/restore, unit isolation, project-admin denial, and source-unavailable execution denial.

- [ ] **Step 2: Run API tests and verify red**

```powershell
backend\.pydeps\Scripts\pytest.exe backend/tests/test_mcp.py -q
```

- [ ] **Step 3: Implement API endpoints and authorization checks**

Use stable client IDs in new endpoints, preserve legacy client-key routes during migration, and return explicit status/phase/error fields. Create operations after configuration commit and make polling idempotent.

- [ ] **Step 4: Run MCP API tests and existing authorization regression**

```powershell
backend\.pydeps\Scripts\pytest.exe backend/tests/test_mcp.py backend/tests/tools/test_registry.py backend/tests/test_agents.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add backend/app/mcp backend/tests/test_mcp.py
git commit -m "feat: 完善MCP客户端管理接口"
```

### Task 6: Frontend MCP management flow

**Files:** `frontend/src/api/mcp.ts`, `frontend/src/views/mcp/McpManageView.vue`, `frontend/src/views/mcp/McpManageView.test.ts`.

- [ ] **Step 1: Write failing component tests**

Assert test button, progress phase, success details, failure retry, auto-sync result, health badges, source-unavailable warning, project grant editor, archive/restore and tool diff labels.

- [ ] **Step 2: Run focused tests and verify red**

```powershell
Set-Location frontend
npm test -- src/views/mcp/McpManageView.test.ts
```

- [ ] **Step 3: Implement API types and UI behavior**

Keep secret fields as credential selectors, never show raw Header values. Poll operations with cancellation on unmount. Disable publish/bind actions when source is unavailable or pending review.

- [ ] **Step 4: Run focused tests and build**

```powershell
npm test -- src/views/mcp/McpManageView.test.ts
npm run build
```

- [ ] **Step 5: Commit**

```powershell
git add frontend/src/api/mcp.ts frontend/src/views/mcp
git commit -m "feat: 完善MCP客户端管理页面"
```

### Task 7: Activity-aware session renewal

**Files:** `frontend/src/App.vue`, `frontend/src/App.test.ts`, `frontend/src/api/auth.ts` if needed.

- [ ] **Step 1: Write failing tests**

Assert visible authenticated pages refresh after recent user activity, hidden/inactive pages do not keep a session alive, only one invalid-session redirect is issued, and the original route query is preserved.

- [ ] **Step 2: Run tests and verify red**

```powershell
npm test -- src/App.test.ts
```

- [ ] **Step 3: Implement activity-aware renewal**

Track pointer/keyboard activity, run a five-minute visible-page check, call `permissionStore.refreshSession()`, and stop the timer on logout/unmount. Do not retry failed mutations automatically.

- [ ] **Step 4: Run tests and build**

```powershell
npm test -- src/App.test.ts
npm run build
```

- [ ] **Step 5: Commit**

```powershell
git add frontend/src/App.vue frontend/src/App.test.ts frontend/src/api/auth.ts
git commit -m "fix: 按用户活动续期登录会话"
```

### Task 8: End-to-end acceptance and review

**Files:** `backend/tests/integration/test_mcp_client_flow.py`, `frontend/src/views/mcp/McpManageView.test.ts` updates if needed, `docs/superpowers/plans` tracking only.

- [ ] **Step 1: Add an ephemeral mock Streamable HTTP/SSE MCP server fixture**

Expose initialize, initialized, paginated tools/list, tool schema change, removal, authentication failure and offline shutdown; use random local ports and clean up in fixture finalizers.

- [ ] **Step 2: Run backend integration acceptance**

```powershell
backend\.pydeps\Scripts\pytest.exe backend/tests/integration/test_mcp_client_flow.py backend/tests/test_mcp.py -q
```

- [ ] **Step 3: Run full backend and frontend verification**

```powershell
backend\.pydeps\Scripts\pytest.exe backend/tests -q
Set-Location frontend
npm test
npm run build
```

- [ ] **Step 4: Verify security and database backup evidence**

Confirm logs contain no credential values, source-unavailable tools cannot execute, unit/project isolation holds, and a pre-migration dump exists under `.local-backups`.

- [ ] **Step 5: Commit acceptance tests and update plan status**

```powershell
git add backend/tests/integration docs/superpowers/plans/2026-08-10-mcp-client-module-plan.md
git commit -m "test: 验收MCP客户端完整流程"
```

After all tasks pass, use `superpowers:verification-before-completion` before claiming completion, then use the branch-finishing workflow for merge/push decisions.
