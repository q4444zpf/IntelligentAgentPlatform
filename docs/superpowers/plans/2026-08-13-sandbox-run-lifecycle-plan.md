# Sandbox Run Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute each platform Run in its isolated Workflow Runner container and persist timeout, cancellation, OOM, Launcher outage, success, and failure as durable Run events and audit records.

**Architecture:** The API-side dispatcher creates an immutable execution snapshot and submits only references to a platform-side coordinator. The coordinator owns leases, deadlines, polling, cancellation, terminal-state persistence, and cleanup; the Launcher owns Docker lifecycle only; the per-Run container owns LangGraph/DeepAgents execution and writes its result/checkpoint through platform-scoped service APIs. Default deployment remains `sandbox=false` until staging acceptance passes.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL, Docker SDK, LangGraph, DeepAgents, MinIO/S3, pytest.

## Global Constraints

- Never expose Launcher tokens, model credentials, passwords, host paths, or raw internal exceptions.
- Launcher must not receive database credentials and Workflow Runner must not receive the Docker Socket.
- Container image, command, workspace, limits, and network policy are platform-controlled.
- Terminal states and error events must be idempotent.
- Container cleanup runs after success, failure, cancellation, timeout, OOM, and Launcher recovery.
- `IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED=false` remains the default.

---

### Task 1: Trusted Per-Run Execution Contract

**Files:**
- Create: `backend/app/runtime/execution_contract.py`
- Create: `backend/app/runtime/run_worker.py`
- Modify: `backend/app/runtime/container_policy.py`
- Modify: `backend/Dockerfile.runner`
- Test: `backend/tests/runtime/test_execution_contract.py`
- Test: `backend/tests/runtime/test_container_policy.py`

**Interfaces:**
- Produces `RunExecutionRequest(run_id, agent_version, checkpoint_key, deadline_at)` with strict validation.
- Produces `RunExecutionResult(status, error_code, artifact_refs, checkpoint_key)` with statuses `completed`, `failed`, `cancelled`.
- `ContainerPolicy.build(...)` uses a fixed worker command and accepts no caller-provided image or shell command.

- [x] Write tests proving untrusted command/image/workspace input cannot enter Docker configuration.
- [x] Run the focused tests and confirm they fail because the execution contract and worker command do not exist.
- [x] Implement the JSON-only contract and fixed `python -m app.runtime.run_worker` container command.
- [x] Run the focused tests and the existing container-policy regression suite.

### Task 2: Observable Launcher Lifecycle

**Files:**
- Modify: `backend/app/runtime/container_launcher.py`
- Modify: `backend/app/runtime/launcher_api.py`
- Modify: `backend/app/runtime/launcher_client.py`
- Test: `backend/tests/runtime/test_container_launcher.py`
- Test: `backend/tests/runtime/test_launcher_api.py`
- Test: `backend/tests/runtime/test_launcher_client.py`

**Interfaces:**
- `inspect(run_id)` returns sanitized `running`, `exited`, `exit_code`, and `oom_killed` fields.
- `terminate(run_id)` is idempotent for an existing Run container.
- `cleanup(run_id)` removes exited or running containers and is idempotent after successful cleanup.

- [x] Write failing tests for normal exit, non-zero exit, OOM, terminate, missing container, and repeated cleanup.
- [x] Run the focused tests and confirm current synthetic `running` output fails them.
- [x] Read Docker state from container attributes without returning raw attributes or host paths.
- [x] Run Launcher and policy regression tests.

### Task 3: Durable Run Lifecycle Coordinator

**Files:**
- Create: `backend/app/runtime/run_lifecycle.py`
- Modify: `backend/app/conversations/dispatcher.py`
- Modify: `backend/app/runtime/workflow_runner.py`
- Test: `backend/tests/runtime/test_run_lifecycle.py`
- Test: `backend/tests/conversations/test_dispatcher.py`

**Interfaces:**
- `SandboxRunCoordinator.execute(run_id)` submits once, polls until terminal, enforces the deadline, persists state, and always requests cleanup.
- Safe error codes: `sandbox_timeout`, `sandbox_oom`, `sandbox_cancelled`, `launcher_unavailable`, `sandbox_failed`.
- Run events: `run.status`, `run.error`, `sandbox.started`, `sandbox.finished`, `sandbox.cleanup`.

- [x] Write failing tests for success, timeout, OOM, cancellation, Launcher outage, duplicate submission, and cleanup failure.
- [x] Confirm every test fails before production code is added.
- [x] Implement idempotent status/event/audit persistence using a fresh SQLAlchemy session per background Run.
- [x] Keep the existing in-process Harness path when sandbox dispatch is disabled.
- [x] Run dispatcher, runtime, audit, conversation, and artifact regression tests.

### Task 4: Cancellation And Recovery API

**Files:**
- Modify: `backend/app/conversations/router.py`
- Modify: `backend/app/conversations/service.py`
- Modify: `backend/app/conversations/dispatcher.py`
- Test: `backend/tests/conversations/test_api.py`
- Test: `backend/tests/runtime/test_run_lifecycle.py`

**Interfaces:**
- `POST /api/agent-runs/{run_id}/cancel` requests cancellation only for a visible Run.
- Cancellation is idempotent and resolves to `cancelled`; unauthorized Runs return `404`.
- Recovery reconciles non-terminal Runs with Launcher state after process restart.

- [x] Write failing API and recovery tests.
- [x] Implement cancellation request persistence and coordinator termination.
- [x] Implement startup reconciliation for leased non-terminal sandbox Runs.
- [x] Run authorization, conversation, runtime, and audit regression tests.

### Task 5: Staging Destructive Acceptance

**Files:**
- Modify: `docs/deployment/sandbox-staging-acceptance.md`
- Create: `docs/deployment/sandbox-staging-results-2026-08-13.md`

**Interfaces:**
- Uses a temporary environment-injected Launcher token and restores `sandbox=false` afterward.
- Records Run row, Run events, audit events, artifacts, container state, and workspace state for each scenario.

- [x] Build the trusted Runner image and start Launcher with a temporary token.
- [x] Execute a deadline-exceeding Run and verify `failed/sandbox_timeout` plus cleanup.
- [x] Execute a memory-exhausting Run and verify `failed/sandbox_oom` plus cleanup.
- [x] Stop Launcher during a running Run and verify `failed/launcher_unavailable`, then restart and reconcile cleanup.
- [x] Confirm no `iap-run-*` containers or unintended artifacts remain.
- [x] Remove Launcher, clear the temporary token, restore `sandbox=false`, and write the evidence-backed result report.
