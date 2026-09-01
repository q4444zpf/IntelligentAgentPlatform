# Acceptance Blockers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the three blockers found during sandbox acceptance and rerun the real acceptance flow.

**Architecture:** Keep artifact access confined to the virtual `/artifacts` tree while accepting the relative paths emitted by DeepAgents. Derive Launcher readiness from the authenticated Workflow Runner boundary instead of exposing the Launcher token to the API. Make PostgreSQL migration tests establish their own schema prerequisites.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/Alembic, DeepAgents, pytest, Docker Compose.

## Global Constraints

- Do not expose Launcher tokens, provider credentials, database passwords, or host paths.
- Preserve the existing internal Runner Gateway network and per-Run container policy.
- Add a failing regression test before each production behavior change.
- Do not revert unrelated working-tree changes.

---

### Task 1: DeepAgents Artifact Paths

**Files:**
- Modify: `backend/tests/runtime/test_artifact_backend.py`
- Modify: `backend/app/runtime/artifact_backend.py`

**Interfaces:**
- Consumes: DeepAgents `BackendProtocol` calls such as `write("result.txt", ...)`, `ls("/")`, and `ls(".")`.
- Produces: paths normalized into the existing virtual `/artifacts` namespace.

- [x] Add regression tests proving relative file paths and root aliases stay inside `/artifacts`.
- [x] Run the focused tests and confirm they fail because relative paths are rejected.
- [x] Implement minimal path normalization without permitting traversal or Windows paths.
- [x] Run ArtifactBackend and SandboxRuntime tests.

### Task 2: Launcher Readiness Status

**Files:**
- Modify: `backend/tests/test_platform.py`
- Modify: `backend/app/platform/router.py`

**Interfaces:**
- Consumes: Workflow Runner `/health` JSON containing `status`, `sandbox`, and `missing`.
- Produces: a `Sandbox Launcher` service status without transferring the Launcher bearer token to the API.

- [x] Add a regression test for `sandbox=true` and one for disabled sandbox mode.
- [x] Run the focused test and confirm the current nonexistent environment flag causes failure.
- [x] Parse Workflow Runner health and map sandbox readiness to healthy, disabled, or unhealthy.
- [x] Run backend platform and frontend dashboard tests.

### Task 3: Migration Test Isolation

**Files:**
- Modify: `backend/tests/integration/test_approval_migration.py`
- Modify: `backend/tests/integration/test_mcp_client_migration.py`
- Modify: `backend/tests/integration/test_identity_migrations.py`

**Interfaces:**
- Consumes: disposable PostgreSQL database through `TEST_DATABASE_URL`.
- Produces: order-independent migration tests with revision-correct expectations.

- [x] Make schema-presence tests explicitly upgrade to head.
- [x] Restrict the identity foundation table set to revision `20260804_09`.
- [x] Run the previously failing multi-file PostgreSQL command against a disposable database.

### Task 4: Acceptance Regression

**Files:**
- No production file changes.

**Interfaces:**
- Consumes: rebuilt API, Workflow Runner, Launcher, PostgreSQL, and MinIO services.
- Produces: recorded evidence for task completion, artifact download, service status, and container cleanup.

- [x] Run focused backend and frontend regression suites.
- [x] Rebuild affected service images and restart the local stack.
- [x] Execute a real sandbox task that writes an Artifact.
- [x] Verify the Artifact can be downloaded and no `iap-run-*` container remains.
