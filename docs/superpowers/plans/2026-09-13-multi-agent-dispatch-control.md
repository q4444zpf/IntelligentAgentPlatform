# Multi-Agent Dispatch Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a production-safe control-command gateway and operations UI on top of the existing published multi-Agent Team runtime.

**Architecture:** Preserve the existing Team aggregate and Runner path. Add a project-scoped control domain whose service validates policy, simulation evidence, approval quorum, validity, idempotency, external execution, feedback, deviation, and physical rollback as explicit states.

**Tech Stack:** FastAPI, SQLAlchemy 2, Pydantic 2, Alembic, PostgreSQL/SQLite tests, Vue 3, TypeScript, Ant Design Vue, Vitest.

## Global Constraints

- Agents only propose control commands; only the control gateway invokes a registered executor.
- Production control always requires a passed simulation and at least one human approver distinct from the requester.
- Dual approval requires two distinct approvers.
- Published Team and Agent snapshots remain immutable and fixed for a Run.
- Existing user changes in runtime Skill injection, compose, and AgentConsole layout must be preserved.

---

### Task 1: Control Domain And State Machine

**Files:**
- Create: `backend/app/control/models.py`
- Create: `backend/app/control/schemas.py`
- Create: `backend/app/control/service.py`
- Create: `backend/app/control/__init__.py`
- Modify: `backend/app/db/base.py`
- Create: `backend/alembic/versions/20260913_28_control_commands.py`
- Test: `backend/tests/control/test_service.py`

**Interfaces:**
- `ControlService.create_policy(context, request) -> ControlPolicyInfo`
- `ControlService.propose(context, request) -> ControlCommandInfo`
- `ControlService.decide(command_id, context, decision, reason) -> ControlCommandInfo`
- `ControlService.execute(command_id, context, request_key) -> ControlCommandInfo`
- `ControlService.record_feedback(command_id, context, request) -> ControlCommandInfo`
- `ControlService.rollback(command_id, context, request_key) -> ControlCommandInfo`
- `ControlExecutor.execute(command, external_idempotency_key) -> ControlExecutorResult`
- `ControlExecutor.rollback(command, rollback_plan, external_idempotency_key) -> ControlExecutorResult`

- [ ] Write service tests for project isolation, JSON Schema/range rejection, simulation Artifact provenance, validity windows, propose idempotency conflict, distinct single/dual approval, expiry, fail-closed executor, execution replay, uncertain outcomes, deviation threshold, and rollback outcomes.
- [ ] Run the focused tests and confirm they fail because the control module is absent.
- [ ] Implement the models, schemas, service, executor protocol, audit recording, and migration with database checks and unique constraints.
- [ ] Run the focused tests until green, then run existing approval, Artifact, identity, and collaboration service tests.

### Task 2: Control API And Authorization Catalogue

**Files:**
- Create: `backend/app/control/router.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/identity/catalogue.py`
- Test: `backend/tests/control/test_api.py`
- Test: `backend/tests/identity/test_bootstrap.py`

**Interfaces:**
- Expose the policy and command endpoints documented in the design.
- Return stable safe error codes for not found, forbidden, conflict, validation, expiry, unavailable executor, and invalid state.

- [ ] Write API tests for CRUD scope, permission gates, approval separation, execution headers, feedback, rollback, safe 404, and stable error responses.
- [ ] Run tests and confirm expected failures.
- [ ] Add the router, application registration, permission catalogue entries, built-in role grants, and menu metadata.
- [ ] Run control API and identity bootstrap tests until green.

### Task 3: Team Persistence Hardening

**Files:**
- Modify: `backend/app/collaboration/models.py`
- Modify: `backend/app/collaboration/repository.py`
- Modify: `backend/alembic/versions/20260913_28_control_commands.py`
- Test: `backend/tests/collaboration/test_service.py`

**Interfaces:**
- `failure_strategy` accepts only `fail_fast` and `continue_then_synthesize` in every persistence path.

- [ ] Add failing tests proving repository and raw persistence reject an unknown failure strategy.
- [ ] Add repository enum validation and a database CHECK constraint in the migration/model metadata.
- [ ] Run collaboration and Team runtime tests until green.

### Task 4: Control Operations UI

**Files:**
- Create: `frontend/src/api/control.ts`
- Create: `frontend/src/api/control.test.ts`
- Create: `frontend/src/views/control/ControlCommandView.vue`
- Create: `frontend/src/views/control/ControlCommandView.test.ts`
- Modify: `frontend/src/router/routes.ts`
- Modify: `frontend/src/stores/permission.ts`
- Test: `frontend/src/router/routes.test.ts`

**Interfaces:**
- List/filter commands and policies; show simulation, approval quorum, validity, execution, deviation, and rollback details.
- Permission-gate approve/reject, execute, feedback, rollback, and policy management actions.

- [ ] Write failing API, route, rendering, permission, and action tests.
- [ ] Implement the typed client, responsive operations view, route/menu, and permission mappings.
- [ ] Run focused Vitest suites until green.

### Task 5: Cross-Module Verification And Documentation

**Files:**
- Modify: `backend/README.md`
- Modify: `frontend/README.md`
- Test: `tests/e2e/test_published_team_run.py`

**Interfaces:**
- Document that Team configuration supplies the supervisor/member topology while the control gateway owns physical command admission and execution.

- [ ] Add an integration scenario that proposes a control command from a Team Run, obtains dual approval, executes once, records feedback, detects deviation, and records rollback provenance.
- [ ] Run backend control/collaboration/runtime regressions, frontend tests, lint/type checks, and the available end-to-end suite.
- [ ] Review the complete diff for secret leakage, unsafe defaults, unrelated edits, and migration-head correctness.
