# Service Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make MinIO restart automatically and expose safe, readable dependency health on the dashboard.

**Architecture:** Add a read-only `/api/platform/services` aggregate beside the existing overview endpoint. Each dependency check maps raw failures to a small safe status contract, and the Vue dashboard polls that contract independently of the overview metrics. Compose owns MinIO restart behavior.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, boto3, Vue 3, TypeScript, Ant Design Vue, Vitest, pytest, Docker Compose.

## Global Constraints

- Service statuses are exactly `healthy`, `unhealthy`, or `disabled`.
- Failure details must not include internal URLs, credentials, filesystem paths, or exception text.
- Dashboard polling interval is exactly `300000` milliseconds.
- Do not modify Agent, Skill, MCP, LangGraph, or DeepAgents execution code.
- Preserve the existing MinIO health check and persistent volume.

---

### Task 1: Safe platform service health API

**Files:**
- Modify: `backend/app/platform/router.py`
- Modify: `backend/tests/test_platform.py`

**Interfaces:**
- Consumes: `SessionFactory`, `IAP_WORKFLOW_RUNNER_HEALTH_URL`, `IAP_OBJECT_STORAGE_*`, and `IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED`.
- Produces: `GET /api/platform/services -> PlatformServices`, `check_service_health(name: str) -> dict[str, str]`.

- [ ] **Step 1: Write failing tests**

Add tests that assert the five service records are returned in the order API, Workflow Runner, PostgreSQL, MinIO, Sandbox Launcher; a successful fake S3 client returns `healthy/available`; a failing fake S3 client returns exactly `unhealthy/unreachable` and never exposes the exception text; sandbox-disabled returns `disabled/not enabled`.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest backend/tests/test_platform.py -q`

Expected: FAIL because `get_services` and `check_service_health` do not exist.

- [ ] **Step 3: Implement the minimal API**

Add `ServiceStatus` and `PlatformServices` Pydantic models, a bounded HTTP checker with a 1.5 second timeout, PostgreSQL `SELECT 1`, MinIO `list_buckets`, sandbox readiness via Workflow Runner, and the `/services` route. Map all failures to fixed safe strings.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest backend/tests/test_platform.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

Commit message: `feat: expose safe platform service health`

---

### Task 2: Compact dashboard service status

**Files:**
- Modify: `frontend/src/api/platform.ts`
- Modify: `frontend/src/views/dashboard/DashboardView.vue`
- Create: `frontend/src/views/dashboard/DashboardView.test.ts`

**Interfaces:**
- Consumes: `GET /platform/services` and its `checked_at`, `services[]` fields.
- Produces: `platformApi.services(signal?)`, compact service cards, manual refresh, and five-minute polling.

- [ ] **Step 1: Write the failing test**

Create a source-level Vitest test asserting the dashboard contains `基础服务状态`, `刷新服务状态`, and `300000`, and no longer contains the duplicate heading controls `API 正常 · v` or `>刷新状态</a-button>`.

- [ ] **Step 2: Run test and verify RED**

Run: `npm test -- DashboardView.test.ts`

Expected: FAIL because the service status panel and polling are absent.

- [ ] **Step 3: Implement minimal UI and API types**

Add `ServiceStatus` and `PlatformServices` TypeScript interfaces and `platformApi.services`. Add a compact grid with one row per service, safe status labels (`正常`, `未启用`, `异常`), last-check time, a small manual refresh button, independent abort controller, and `window.setInterval(refreshServices, 300000)` cleanup.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `npm test -- DashboardView.test.ts`

Expected: all dashboard tests pass.

- [ ] **Step 5: Commit**

Commit message: `feat: show dependency health on dashboard`

---

### Task 3: MinIO restart policy and regression coverage

**Files:**
- Modify: `compose.yaml`
- Create: `backend/tests/test_compose_service_stability.py`

**Interfaces:**
- Consumes: Docker Compose service definition for `minio`.
- Produces: MinIO restart policy `unless-stopped` without changing its health check, ports, or `minio-data:/data` volume.

- [ ] **Step 1: Write the failing test**

Load `compose.yaml` with `yaml.safe_load` and assert `services.minio.restart == "unless-stopped"`, the health check remains present, and `minio-data:/data` remains mounted.

- [ ] **Step 2: Run test and verify RED**

Run: `python -m pytest backend/tests/test_compose_service_stability.py -q`

Expected: FAIL because MinIO has no restart policy.

- [ ] **Step 3: Add the minimal Compose setting**

Add exactly `restart: unless-stopped` to the `minio` service.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest backend/tests/test_compose_service_stability.py -q`

Expected: all tests pass.

- [ ] **Step 5: Run phase regression tests**

Run: `python -m pytest backend/tests/test_platform.py backend/tests/test_compose_service_stability.py -q`

Run: `npm test -- DashboardView.test.ts`

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

Commit message: `fix: restart minio after unexpected exits`
