# Approval Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persisted, permission-aware approval workflow for high-risk tool calls and expose it through the `/approvals` page.

**Architecture:** Store approval requests beside conversation runs and tool invocations. The Tool Gateway creates a pending request and moves the run to `waiting_approval`; an approval service performs scoped authorization, expiry, snapshot-integrity, and single-decision checks, then resumes the run through the dispatcher. The frontend consumes dedicated approval APIs and renders a real pending/history table.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Pydantic, Vue 3, Ant Design Vue, Vitest.

## Global Constraints

- Use TDD: every production behavior starts with a failing test and is re-run after implementation.
- Keep unit/project scope from `RequestContext`; never authorize across units or projects.
- Do not expose raw tool credentials or unsanitized arguments; use Gateway summaries and digests.
- Preserve local development identity and existing low-risk automatic tool execution.
- Database schema changes require an Alembic migration and a local backup before migration.
- Do not commit secrets or generated build output.

### Task 1: Approval persistence and service

**Files:**
- Create: `backend/app/approvals/models.py`, `backend/app/approvals/schemas.py`, `backend/app/approvals/service.py`, `backend/app/approvals/__init__.py`
- Modify: `backend/app/db/base.py`
- Create: `backend/alembic/versions/20260810_15_approval_workflow.py`
- Test: `backend/tests/approvals/test_service.py`

**Interfaces:** `ApprovalService.create_request(...)`, `list_pending(...)`, `get(...)`, `approve(...)`, `reject(...)`; statuses are `pending`, `approved`, `rejected`, `expired`, `cancelled`.

- [x] Write tests for pending creation, scoped listing, ordinary-user rejection, self-approval rejection, argument-digest mismatch, duplicate decision rejection, and reject-terminal behavior.
- [x] Run `python -m pytest tests/approvals/test_service.py -q` and confirm the missing-module failure.
- [x] Implement the SQLAlchemy model, service rules, schemas, and import model from `Base`.
- [x] Add the Alembic migration after backing up the local development database.
- [x] Re-run the service tests and the migration tests.

### Task 2: Gateway pause and run resume

**Files:**
- Modify: `backend/app/tools/gateway.py`, `backend/app/runtime/harness.py`, `backend/app/conversations/models.py`, `backend/app/conversations/repository.py`, `backend/app/conversations/dispatcher.py`
- Test: `backend/tests/approvals/test_gateway.py`, `backend/tests/conversations/test_dispatcher.py`

- [x] Add failing tests proving `requires_approval` creates a pending request, `waiting_approval` is persisted with `approval.requested`, and approved calls resume only after digest validation.
- [x] Implement the smallest Gateway interception and dispatcher resume path; low-risk tools retain current behavior.
- [x] Re-run focused tests and existing gateway/harness suites.

### Task 3: Approval API

**Files:**
- Create: `backend/app/approvals/router.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/approvals/test_api.py`

- [x] Add failing API tests for list/detail/approve/reject, 401/403/404/409/410 responses, and project/unit isolation.
- [x] Implement routes under `/api/approvals` using `require_request_context` and approval permission checks.
- [x] Re-run API tests and the complete backend suite.

### Task 4: Approvals frontend

**Files:**
- Create: `frontend/src/api/approvals.ts`, `frontend/src/views/security/ApprovalListView.vue`, `frontend/src/views/security/ApprovalListView.test.ts`
- Modify: `frontend/src/router/routes.ts`

- [x] Add failing Vitest coverage for pending list, detail, approve/reject actions, loading, empty, and 403/409/expired error states.
- [x] Implement the API client and table/detail drawer with risk, tool, parameter summary, requester, expiry, and decision controls.
- [x] Replace the `/approvals` placeholder route and run frontend tests/build.

### Task 5: Verification and handoff

- [x] Run backend tests, frontend tests/build, Alembic upgrade, Docker health check, and API smoke tests.
- [x] Review the diff and document any manual browser steps that require the user to be logged in.
- [x] Commit the isolated branch only after all verification passes; ask before pushing or merging.
