# Published Team Review Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the release-blocking trust, authorization, reproducibility, recovery, and API gaps found in the final review of Published Multi-Agent Team Foundation.

**Architecture:** Keep Team management project-scoped, but move every privileged decision to server-owned services. Publication captures canonical immutable Agent definitions; execution consumes only the selected Team version; Runner Gateway validates all Team provenance and persists resumable scheduler state. Frontend controls mirror server permissions and contracts without owning security decisions.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, Pydantic 2, PostgreSQL, LangGraph/DeepAgents, Pytest, Vue 3, TypeScript, Pinia, Ant Design Vue, Vitest.

## Global Constraints

- Never trust client-supplied Agent definitions, digests, permission decisions, or Artifact provenance.
- Every Team lookup and Team version resolution must remain unit/project scoped and fail closed.
- Published Team versions and member rows are immutable, including insert and reassignment paths.
- A Run executes the exact Team and Agent definitions captured at message acceptance.
- Effective member capability authorization is the intersection of initiating-user access, Team whitelist, Agent bindings, member whitelist, and current capability availability/policy.
- Approval resume must not replay completed tasks or uncertain side effects.
- Serial execution remains valid when the published limit is one; parallel execution must never exceed Team and Runner ceilings.
- Preserve legacy single-Agent snapshot and execution behavior.

---

### Task 1: Scoped Collaboration Authorization And Team Audit

**Files:**
- Modify: `backend/app/core/request_context.py`
- Modify: `backend/app/collaboration/router.py`
- Modify: `backend/app/collaboration/service.py`
- Modify: `backend/app/conversations/service.py`
- Test: `backend/tests/collaboration/test_service.py`
- Test: `backend/tests/collaboration/test_api.py`
- Test: `backend/tests/conversations/test_service.py`

**Interfaces:**
- Consumes: `AuthorizationContext`, `PermissionGrant`, and the identity permission evaluator used by `require_permission`.
- Produces: Team service methods that receive a permission-aware context and enforce `collaboration.read`, `collaboration.manage`, or `collaboration.run` at the current project.
- Preserves: development-header tests by deriving the same catalogue grants as cookie authentication.

- [ ] **Step 1: Write failing permission tests**

Add tests proving a non-admin context with a project-scoped `collaboration.manage` grant can create/publish, a context without the exact grant receives `403`, and a grant for another project cannot read or run the Team.

- [ ] **Step 2: Write failing Team audit tests**

Assert Team create/update/publish/enable actions record `resource_type="team"`, and Team message acceptance records Team ID, selected version ID, Run ID, project scope, and initiating roles. Assert single-Agent audit remains `resource_type="agent"`.

- [ ] **Step 3: Run focused tests and verify the admin shortcut and Agent audit fail**

Run: `python -m pytest backend/tests/collaboration backend/tests/conversations/test_service.py -q`

- [ ] **Step 4: Implement permission-aware context wiring**

Use the repository's existing `AuthorizationContext`/permission evaluator. Do not infer permissions from `role == "admin"`; route dependencies must supply the current project authorization snapshot and Team service must require the exact permission code.

- [ ] **Step 5: Implement Team lifecycle and Run audits**

Record stable Team audit actions in the same transaction as each mutation. Branch message audit by actor type and include `actor_version_id` only for Team Runs.

- [ ] **Step 6: Run identity, collaboration, conversation, and audit regressions**

Run: `python -m pytest backend/tests/identity backend/tests/collaboration backend/tests/conversations backend/tests/audit -q`

- [ ] **Step 7: Commit**

```bash
git add backend/app/core/request_context.py backend/app/collaboration backend/app/conversations/service.py backend/tests/collaboration backend/tests/conversations/test_service.py
git commit -m "fix: enforce scoped team permissions"
```

### Task 2: Trusted Publication And Immutable Team Snapshots

**Files:**
- Modify: `backend/app/collaboration/schemas.py`
- Modify: `backend/app/collaboration/service.py`
- Modify: `backend/app/collaboration/repository.py`
- Modify: `backend/app/runtime/execution_snapshot.py`
- Modify: `backend/alembic/versions/20260816_21_published_teams.py`
- Test: `backend/tests/collaboration/test_service.py`
- Test: `backend/tests/runtime/test_execution_snapshot.py`
- Test: `backend/tests/integration/test_postgres_migrations.py`

**Interfaces:**
- Consumes: `AgentService.get(agent_id)`, Agent tool/skill bindings, current capability services, and scoped Team repository reads.
- Produces: server-computed canonical member definitions/digests and `PublishedTeamSnapshot` members carrying complete executable definitions.

- [ ] **Step 1: Write failing forged-definition and unavailable-resource tests**

Assert draft requests cannot submit `agent_definition` or `agent_definition_digest`; publication rejects missing, disabled, or cross-project Agents and whitelist values outside Team/Agent/current availability intersections.

- [ ] **Step 2: Write failing reproducibility and scope tests**

Publish a Team, edit/disable the live Agent, then assert snapshot creation still returns the original embedded prompt/model/tools. Assert an `actor_version_id` belonging to another Team/project is rejected.

- [ ] **Step 3: Write failing PostgreSQL immutability tests**

After publication, direct `INSERT`, `UPDATE`, reassignment, and `DELETE` of member rows must all fail; draft member replacement must still succeed before publication.

- [ ] **Step 4: Run focused tests and confirm current client snapshot/live Agent behavior fails**

Run: `python -m pytest backend/tests/collaboration/test_service.py backend/tests/runtime/test_execution_snapshot.py backend/tests/integration/test_postgres_migrations.py -q`

- [ ] **Step 5: Capture canonical Agent definitions on publish**

Remove snapshot fields from write schemas. Resolve every member server-side, validate enabled/scoped resources and whitelist subsets, serialize the complete executable Agent definition with sorted-key UTF-8 JSON, and compute SHA-256 digests in the publish transaction.

- [ ] **Step 6: Build snapshots only from selected published rows**

Load the version through a scoped Team/version join, verify `team_id == run.actor_id`, verify Team/member digests, and populate each member's immutable model, prompts, bindings, and policies without calling the live Agent service.

- [ ] **Step 7: Harden PostgreSQL immutability triggers**

Cover `INSERT OR UPDATE OR DELETE`; check `NEW.team_version_id` and `OLD.team_version_id` as applicable. Order repository publication so draft members are copied before the target version becomes published.

- [ ] **Step 8: Run focused and migration regressions**

Run: `python -m pytest backend/tests/collaboration backend/tests/runtime/test_execution_snapshot.py backend/tests/integration/test_postgres_migrations.py -q`

- [ ] **Step 9: Commit**

```bash
git add backend/app/collaboration backend/app/runtime/execution_snapshot.py backend/alembic/versions/20260816_21_published_teams.py backend/tests/collaboration backend/tests/runtime/test_execution_snapshot.py backend/tests/integration/test_postgres_migrations.py
git commit -m "fix: capture trusted team snapshots"
```

### Task 3: Resumable Bounded Team Runtime And Provenance

**Files:**
- Modify: `backend/app/runtime/team_graph.py`
- Modify: `backend/app/runtime/sandbox_runtime.py`
- Modify: `backend/app/runtime/runner_gateway_schemas.py`
- Modify: `backend/app/runtime/runner_gateway_service.py`
- Modify: `backend/app/runtime/artifact_backend.py`
- Test: `backend/tests/runtime/test_team_graph.py`
- Test: `backend/tests/runtime/test_sandbox_runtime.py`
- Test: `backend/tests/runtime/test_runner_gateway_api.py`
- Test: `backend/tests/runtime/test_artifact_backend.py`
- Test: `tests/e2e/test_published_team_run.py`

**Interfaces:**
- Consumes: immutable `PublishedTeamSnapshot`, Runner limits, Gateway checkpoints/events/cancellation, and member-specific effective capabilities.
- Produces: validated supervisor plans, dependency-ready bounded batches, deterministic joins, resumable scheduler checkpoints, and verified Artifact provenance.

- [ ] **Step 1: Write failing scheduler tests**

Cover unknown members, cycles, duplicate task IDs/positions, `max_steps`, parallel width, `max_subagents`, dependency-ready batching, deterministic positional joins, cancellation before dequeue/synthesis, fail-fast, and continue-then-synthesize partial output.

- [ ] **Step 2: Write failing resume/idempotency tests**

Interrupt a member Tool call for approval, recreate the Sandbox runtime, resume, and assert completed tasks/events are not replayed, event sequence continues, and the exact member/task invocation resumes once.

- [ ] **Step 3: Write failing provenance boundary tests**

For Team Runs, Artifact creation without provenance or with wrong version/member/task must fail at Runner Gateway. For Agent Runs, Team provenance must be rejected and existing Artifact creation must remain compatible.

- [ ] **Step 4: Run focused tests and verify placeholder scheduling/recovery fails**

Run: `python -m pytest backend/tests/runtime/test_team_graph.py backend/tests/runtime/test_sandbox_runtime.py backend/tests/runtime/test_runner_gateway_api.py backend/tests/runtime/test_artifact_backend.py tests/e2e/test_published_team_run.py -q`

- [ ] **Step 5: Implement validated planning and bounded scheduling**

Generate/parse the supervisor plan through the platform-owned schema; execute dependency-ready batches up to `min(team.max_parallel_members, runner.max_subagents)`; build each member with its own immutable definition and effective capabilities; join results by task position.

- [ ] **Step 6: Persist and restore complete Team scheduler state**

Checkpoint event sequence, pending queue, completed/failed results, active member/task, invocation identity, Team version, and snapshot digest. Resume at the interrupted node; never rerun completed tasks or automatically replay uncertain side effects.

- [ ] **Step 7: Enforce partial response and Artifact provenance**

Include failed task details in synthesis and guarantee final content states partial completion. Gateway must validate provenance against the immutable Team snapshot and accepted plan, deriving server-owned fields where possible.

- [ ] **Step 8: Run runtime, approval, Gateway, Artifact, and E2E regressions**

Run: `python -m pytest backend/tests/runtime backend/tests/approvals backend/tests/artifacts tests/e2e/test_published_team_run.py -q`

- [ ] **Step 9: Commit**

```bash
git add backend/app/runtime backend/tests/runtime tests/e2e/test_published_team_run.py
git commit -m "fix: resume bounded team execution"
```

### Task 4: Align Team Frontend Contracts And Controls

**Files:**
- Modify: `backend/app/collaboration/router.py`
- Modify: `frontend/src/api/teams.ts`
- Modify: `frontend/src/views/collaboration/TeamManageView.vue`
- Modify: `frontend/src/views/collaboration/TeamManageView.test.ts`
- Modify: `frontend/src/views/agent/AgentConsoleView.vue`
- Test: `frontend/src/api/teams.test.ts`

**Interfaces:**
- Consumes: server Team filters, permission capabilities, Agent/tool/skill/knowledge catalogues, and approval policies.
- Produces: accurate filtered Team lists and permission-aware management controls that never submit Agent snapshots.

- [ ] **Step 1: Write failing API contract tests**

Assert `enabled` and `published` query filters affect backend results, Team creation has no ignored client ID, and draft payloads contain only user-editable IDs/responsibilities/whitelists/limits/policy.

- [ ] **Step 2: Write failing permission and editor tests**

Assert readers cannot mutate, project operators with `collaboration.manage` can mutate, and the editor supports Team/member Tool, Skill, knowledge, and approval-policy controls with field-level validation.

- [ ] **Step 3: Run focused tests and verify current contract drift**

Run: `python -m pytest backend/tests/collaboration/test_api.py -q`

Run: `npm test -- --run src/api/teams.test.ts src/views/collaboration/TeamManageView.test.ts src/views/agent/AgentConsoleView.test.ts`

- [ ] **Step 4: Align backend filters and frontend payloads**

Implement typed `enabled`/`published` filters server-side, remove client-specified Team IDs and snapshot fields, and derive action visibility from returned permission capabilities rather than admin role.

- [ ] **Step 5: Complete management controls**

Use existing operational form patterns and scoped catalogue APIs. Keep the layout responsive and preserve empty/read-only states.

- [ ] **Step 6: Run frontend full regression and production build**

Run: `npm test -- --run`

Run: `npm run build`

- [ ] **Step 7: Commit**

```bash
git add backend/app/collaboration/router.py frontend/src/api/teams.ts frontend/src/api/teams.test.ts frontend/src/views/collaboration frontend/src/views/agent/AgentConsoleView.vue
git commit -m "fix: align team management contracts"
```

### Task 5: Final Security And Regression Acceptance

**Files:**
- Modify only regression tests or documentation when a verified contract needs correction.

- [ ] **Step 1: Run full backend regression**

Run: `python -m pytest backend -q --basetemp backend/.tmp-team-remediation-full`

- [ ] **Step 2: Run PostgreSQL migration suite**

Run: `python -m pytest backend/tests/integration/test_postgres_migrations.py -q`

- [ ] **Step 3: Run full frontend regression and production build**

Run: `npm test -- --run`

Run: `npm run build`

- [ ] **Step 4: Run root Team E2E and security boundaries**

Run: `python -m pytest tests/e2e/test_published_team_run.py backend/tests/runtime/test_runner_gateway_nginx_boundary.py backend/tests/collaboration backend/tests/identity/test_authorization.py -q`

- [ ] **Step 5: Run independent whole-branch review**

Review `git diff main...HEAD` against both the original design and this remediation plan. No Critical or Important finding may remain.

- [ ] **Step 6: Fetch, merge, reverify, integrate, and push**

Follow `fetch -> merge origin/main -> focused/full verification -> merge to local main -> full verification -> push origin main`. Preserve unrelated untracked files in the main workspace.
