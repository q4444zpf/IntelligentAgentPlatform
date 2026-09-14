# Multi-Agent Control Review Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every merge-blocking safety, integration, idempotency, audit, API, migration, and operations-UI gap found in the multi-agent dispatch-control review.

**Architecture:** Keep the published Team runtime as the only trusted producer of simulation attestations and keep all physical execution behind the control gateway. Persist a pending execution attempt before any external call, use a configuration-built HTTPS executor registry, hold successful calls in `awaiting_feedback`, and make every database transition atomic with its audit event. Expose stable structured errors and complete project-scoped policy, command, detail, feedback, and filter workflows in the API and Vue console.

**Tech Stack:** FastAPI, SQLAlchemy 2, Pydantic 2, Alembic, PostgreSQL/SQLite, httpx, Vue 3, TypeScript, Ant Design Vue, Vitest, pytest.

## Global Constraints

- Agents may create ordinary artifacts and propose commands, but cannot assert that a simulation succeeded or invoke a physical executor directly.
- Only the runner gateway may attach `SimulationAttestation`, and only after validating a completed simulation-designated Team task and matching artifact capability.
- Production control requires a successful digest-bound simulation attestation, a valid published Team Run binding when supplied, a valid command window, and one or two distinct human approvals excluding the requester.
- External calls use HTTPS in production, do not follow redirects or trust proxy environment variables, carry a secret-backed bearer token, and reuse one stable external idempotency key per command and operation kind.
- A successful executor response is not terminal success; only fresh feedback evaluated against the command's snapshotted thresholds may transition `awaiting_feedback` to `succeeded`.
- Database rollback is never described as physical rollback. Failed or uncertain external outcomes remain explicit and recoverable.
- Every persisted control mutation and its audit event commit in the same database transaction. No raw prompts, credentials, executor response bodies, or customer measurements enter audit metadata.
- Existing Agent/Team APIs, immutable published snapshots, generic artifact uploads, and non-control health behavior remain backward compatible.
- Do not rewrite unrelated runtime Skill injection, compose, or AgentConsole changes.

## File Responsibility Map

- `backend/app/runtime/team_graph.py`: typed Team task output contract and completed-task artifact IDs.
- `backend/app/runtime/runner_gateway_schemas.py`: trusted simulation-attestation request/response contracts.
- `backend/app/runtime/artifact_backend.py`: invocation-local tracking of created artifact IDs; it never creates attestations.
- `backend/app/runtime/runner_gateway_client.py`: internal runner attestation client.
- `backend/app/runtime/runner_gateway_router.py`: run-token-protected attestation endpoint.
- `backend/app/runtime/runner_gateway_service.py`: capability, checkpoint, task-kind, and digest verification before attestation.
- `backend/app/artifacts/service.py`: scoped, idempotent persistence of the gateway-issued attestation.
- `backend/app/control/models.py`: command, attempt, feedback, policy snapshot, and state constraints.
- `backend/app/control/schemas.py`: closed public request/response types and filters.
- `backend/app/control/service.py`: authorization, state transitions, durable external-call protocol, feedback evaluation, and atomic audit writes.
- `backend/app/control/executors.py`: configuration parser, HTTP adapter, registry, strict result parsing, and readiness checks.
- `backend/app/control/errors.py`: stable control error codes and FastAPI handler.
- `backend/app/control/router.py`: dependency injection and complete resource-oriented API.
- `backend/app/core/settings.py` and `backend/app/main.py`: environment construction, startup validation, executor injection, and health readiness.
- `frontend/src/api/control.ts`: full typed control API.
- `frontend/src/api/client.ts`: structured API error decoding.
- `frontend/src/views/control/ControlCommandView.vue`: orchestration, filters, policy/command lists, and action state.
- `frontend/src/views/control/components/*.vue`: focused proposal, policy, feedback, and detail surfaces.
- `backend/alembic/versions/20260913_28_control_commands.py`: safe preflight for legacy `failure_strategy` values.
- `backend/alembic/versions/20260915_31_control_execution_hardening.py`: state, snapshot, attempt, feedback, and idempotency schema changes.

## Task Dependencies

1. Task 1 is independent and defines trusted evidence and Team Run binding.
2. Task 2 depends on Task 1's attestation contract.
3. Task 3 depends on Task 2's closed executor result and attempt contracts.
4. Task 4 depends on Tasks 1-3 and owns atomic audit plus the public API contract.
5. Task 5 depends on Task 4's final API.
6. Task 6 depends on all earlier tasks and is the release gate.

---

### Task 1: Trusted Team Simulation Attestation And Run Binding

**Files:**
- Modify: `backend/app/runtime/team_graph.py`
- Modify: `backend/app/runtime/runner_gateway_schemas.py`
- Modify: `backend/app/runtime/artifact_backend.py`
- Modify: `backend/app/runtime/runner_gateway_client.py`
- Modify: `backend/app/runtime/runner_gateway_router.py`
- Modify: `backend/app/runtime/runner_gateway_service.py`
- Modify: `backend/app/runtime/run_tokens.py`
- Modify: `backend/app/runtime/run_lifecycle.py`
- Modify: `backend/app/runtime/sandbox_runtime.py`
- Modify: `backend/app/artifacts/service.py`
- Modify: `backend/app/control/schemas.py`
- Modify: `backend/app/control/service.py`
- Test: `backend/tests/runtime/test_artifact_backend.py`
- Test: `backend/tests/runtime/test_runner_gateway_api.py`
- Test: `backend/tests/runtime/test_runner_gateway_state.py`
- Test: `backend/tests/runtime/test_sandbox_runtime.py`
- Test: `backend/tests/control/test_service.py`

**Interfaces:**
- `TeamTaskOutputContract.artifact_kind: Literal["generic", "simulation"] = "generic"`
- `TeamTaskResult.artifact_ids: tuple[str, ...] = ()`
- `SimulationAttestation(kind="simulation", status="succeeded", completed_at, artifact_sha256)`
- `ArtifactProvenance.simulation_attestation: SimulationAttestation | None = None`
- `SimulationAttestationRequest(team_version_id, member_agent_id, task_id, invocation_id, artifact_ids, completed_at, capability)`
- `RunnerGatewayClient.attest_simulation_artifacts(*, team_version_id: str, member_agent_id: str, task_id: str, invocation_id: str, artifact_ids: tuple[str, ...], completed_at: datetime, capability: str, idempotency_key: str) -> list[ArtifactFileResponse]`
- `RunnerGatewayService.attest_simulation_artifacts(run_id, claims, request) -> list[ArtifactRecord]`
- `ArtifactService.attest_completed_simulation(run_id, binding, artifact_ids, completed_at) -> list[ArtifactRecord]`
- `ControlCommandProposeRequest.team_version_id: str | None`; remove the ambiguous `team_version` name before this unreleased API merges.
- `ControlService._validated_run_binding(context, run_id, team_version_id, artifact) -> AgentRun | None`

- [ ] **Step 1: Add failing contract and trust-boundary tests**

Add these exact tests:

Add `test_artifact_backend_tracks_ids_without_self_attesting` and assert the backend exposes exactly the IDs returned by its create calls while the submitted provenance contains no attestation. Add `test_runner_rejects_simulation_attestation_before_task_completion` and assert HTTP 409 with `team_task_not_completed`. Add `test_runner_attests_only_artifacts_from_matching_completed_simulation_task` and assert mismatched bindings fail while matching artifacts receive a digest-bound attestation. Add `test_team_runtime_attests_completed_simulation_artifacts` and assert the attestation call occurs after `team.task.completed`. Add `test_propose_rejects_forged_simulation_metadata`, `test_propose_accepts_gateway_attestation_bound_to_same_run_and_digest`, `test_propose_rejects_mismatched_or_unpublished_team_version_id`, and `test_generic_team_artifact_provenance_remains_backward_compatible` with the named admission and compatibility assertions.

The successful attestation assertion must compare `artifact_sha256` with `ArtifactRecord.sha256`, and the rejection tests must cover wrong project, wrong Run, wrong invocation, generic task kind, incomplete checkpoint, and an agent-supplied attestation in `ArtifactCreateRequest`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
cd backend
python -m pytest tests/runtime/test_artifact_backend.py tests/runtime/test_runner_gateway_api.py tests/runtime/test_runner_gateway_state.py tests/runtime/test_sandbox_runtime.py tests/control/test_service.py -q -p no:cacheprovider
```

Expected: FAIL because `artifact_kind`, `artifact_ids`, `SimulationAttestation`, the attestation endpoint, and `team_version_id` validation do not exist.

- [ ] **Step 3: Add the typed simulation designation and attestation schema**

Implement these closed models; keep `ArtifactCreateRequest.provenance` unable to accept `simulation_attestation` from the sandbox create call:

```python
class SimulationAttestation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["simulation"] = "simulation"
    status: Literal["succeeded"] = "succeeded"
    completed_at: datetime
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

class SimulationAttestationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team_version_id: str = Field(min_length=1, max_length=128)
    member_agent_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=128)
    invocation_id: str = Field(min_length=1, max_length=256)
    artifact_ids: tuple[str, ...] = Field(min_length=1, max_length=100)
    completed_at: datetime
    capability: str = Field(min_length=32, max_length=256)
```

Add `artifact_kind` to the existing frozen output contract and update the supervisor planning prompt to emit it explicitly. Unknown values must fail `parse_supervisor_plan`; default `generic` preserves existing plans and checkpoints.

- [ ] **Step 4: Implement post-completion attestation**

Track created IDs inside each invocation-local `ArtifactBackend`. Return them with the member result, store them in `TeamTaskResult`, and call the new gateway method only after the task has been persisted with `checkpoint_status="completed"` and only when `artifact_kind == "simulation"`. Recovery must call the same method with the same request digest and be idempotent.

Protect `POST /internal/runner/runs/{run_id}/artifact-attestations/simulation` with a new `RunnerAction` value `artifact.attest`; include it in newly issued Team Run tokens. In `RunnerGatewayService`, verify the snapshot digest, run scope, reserved capability binding, latest Team scheduler checkpoint, task output kind, completed task result, invocation identity, and every artifact's run/provenance binding before calling `ArtifactService`. The service, not the sandbox request, constructs `SimulationAttestation` and sets `artifact_sha256` from the stored record.

- [ ] **Step 5: Consume only trusted attestation in control admission**

Replace the loose `kind/status` lookup with strict `SimulationAttestation.model_validate`. Require `artifact.sha256 == attestation.artifact_sha256`; when `run_id` or `team_version_id` is present require both, load the project-scoped `AgentRun`, require `actor_type == "team"`, require `AgentRun.actor_version_id == team_version_id`, and match all provenance identifiers. Do not accept user-uploaded artifacts or untyped provenance maps.

- [ ] **Step 6: Run GREEN and commit**

Run the Step 2 command, then:

```powershell
python -m pytest tests/integration/test_runner_gateway_artifacts.py ../tests/e2e/test_published_team_run.py -q -p no:cacheprovider
git add app/runtime app/artifacts/service.py app/control/schemas.py app/control/service.py tests/runtime tests/control/test_service.py ../tests/e2e/test_published_team_run.py
git commit -m "fix: trust Team simulation attestations"
```

Expected: all selected tests PASS; generic artifacts and existing Team plans remain valid.

---

### Task 2: Durable Execution Attempts, Awaiting Feedback, And Kind-Scoped Replay

**Files:**
- Modify: `backend/app/control/models.py`
- Modify: `backend/app/control/schemas.py`
- Modify: `backend/app/control/service.py`
- Create: `backend/alembic/versions/20260915_31_control_execution_hardening.py`
- Test: `backend/tests/control/test_service.py`
- Test: `backend/tests/control/test_execution_recovery.py`

**Interfaces:**
- `ControlExecutionOutcome = Literal["succeeded", "failed", "unknown"]`
- `ControlCommandStatus` adds `awaiting_feedback`.
- `ControlExecutionAttempt` adds `external_id`, `completed_at`, and unique `(command_id, kind, request_key)`.
- `ControlFeedbackRecord(command_id, recorder_id, observed_at, actual_state, deviation_result, created_at)` is append-only.
- `ControlCommand` adds `policy_digest`, `deviation_thresholds_snapshot`, and `feedback_observed_at`; rename stored `team_version` to `team_version_id`.
- `ControlService._begin_attempt(command, kind, request_key) -> ControlExecutionAttempt`
- `ControlService._complete_attempt(command_id, attempt_id, result) -> ControlCommandInfo`
- Same `(command_id, kind, request_key)` replays or reconciles; a different key while that kind is pending returns `control_execution_in_progress` without an external call.

- [ ] **Step 1: Write the state-machine and recovery tests**

Add `test_execute_commits_pending_attempt_before_external_call`, with the fake executor opening a second session and observing `outcome="pending"` before returning. Add `test_execute_reconciles_pending_attempt_with_same_external_key_after_timeout` and assert two transport calls carry one external key and resolve one logical operation. Add `test_execute_rejects_different_key_while_attempt_is_pending` and assert no second transport call. Add `test_invalid_executor_outcome_is_persisted_as_unknown`, `test_executor_success_waits_for_feedback_before_terminal_success`, `test_feedback_persists_observed_at_and_rejects_stale_or_out_of_order_readings`, `test_execute_and_rollback_may_reuse_client_key_because_kind_scopes_it`, `test_successful_rollback_replays_with_same_key_without_second_external_call`, and `test_policy_threshold_snapshot_not_later_policy_edit_drives_deviation` with assertions matching their names.

The timeout fake must record that a physical call happened before raising, then return the original result when called again with the same external key.

- [ ] **Step 2: Run RED**

Run:

```powershell
cd backend
python -m pytest tests/control/test_service.py tests/control/test_execution_recovery.py -q -p no:cacheprovider
```

Expected: FAIL on missing `awaiting_feedback`, missing feedback records, cross-kind key collision, and stranded `executing` behavior.

- [ ] **Step 3: Add migration and closed persistence types**

Create revision `20260915_31` with `down_revision="20260914_30"`. Add the new command snapshot/timestamp columns, rename `team_version` to `team_version_id`, add `awaiting_feedback` to the status CHECK, add attempt result columns, replace `uq_control_execution_request` with `(command_id, kind, request_key)`, and create `control_feedback_records` with project-independent FK ownership through `command_id`. Backfill existing `succeeded` commands to `awaiting_feedback` when `actual_state IS NULL`; preserve already evaluated successes.

- [ ] **Step 4: Implement the two-phase durable protocol**

Before any external call, lock the command, resolve replay by `(command_id, kind, request_key)`, validate state/window, create an attempt with `outcome="pending"`, set `executing` or `rollback_pending`, and commit. Call the adapter only after that commit. Parse its response into `ControlExecutorResult`; convert invalid/exceptional responses to closed `unknown`, then lock and complete the existing attempt. Executor success transitions execute to `awaiting_feedback`, not `succeeded`.

For a pending same-key replay, invoke the adapter again with the stored external idempotency key to obtain the adapter's replayed result. Never generate a second external key. Completed same-key attempts return the current command without calling the adapter.

- [ ] **Step 5: Make feedback fresh and append-only**

If `observed_at` is omitted, use the server receipt clock for compatibility. Require timezone awareness, `observed_at >= execute_attempt.completed_at`, `observed_at <= now + 5 minutes`, and monotonic increase over `feedback_observed_at`. Insert `ControlFeedbackRecord`, update the command's latest actual/deviation fields, and allow `awaiting_feedback` to become only `succeeded` or `deviation`.

- [ ] **Step 6: Run GREEN and commit**

```powershell
python -m pytest tests/control/test_service.py tests/control/test_execution_recovery.py -q -p no:cacheprovider
git add app/control alembic/versions/20260915_31_control_execution_hardening.py tests/control
git commit -m "fix: make control execution recoverable"
```

Expected: all selected tests PASS and external-call count remains one logical operation across replay.

---

### Task 3: Configuration-Built HTTP Executor Registry And Fail-Closed Readiness

**Files:**
- Create: `backend/app/control/executors.py`
- Modify: `backend/app/core/settings.py`
- Modify: `backend/app/control/router.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/control/test_executors.py`
- Test: `backend/tests/control/test_startup.py`
- Test: `backend/tests/core/test_settings.py`

**Interfaces:**
- `ControlExecutorEndpoint(asset_type, base_url, token_env, timeout_seconds)`
- `ControlExecutorRegistry.from_json(value, secret_reader, environment, http_transport=None) -> ControlExecutorRegistry`
- `ControlExecutorRegistry.executors: Mapping[str, ControlExecutor]`
- `ControlExecutorRegistry.readiness() -> ControlReadiness(ready, unavailable_asset_types)`
- `HttpControlExecutor.execute(command, external_idempotency_key) -> ControlExecutorResult`
- `HttpControlExecutor.rollback(command, rollback_plan, external_idempotency_key) -> ControlExecutorResult`
- Environment: `IAP_CONTROL_GATEWAY_ENABLED` and `IAP_CONTROL_EXECUTORS_JSON`.
- JSON shape: `{"reservoir":{"base_url":"https://control.example/api","token_env":"IAP_CONTROL_EXECUTOR_RESERVOIR_TOKEN","timeout_seconds":10}}`.

- [ ] **Step 1: Write failing configuration, transport, injection, and health tests**

Add `test_production_rejects_enabled_control_without_executor_configuration`, `test_production_rejects_http_executor_url_and_missing_secret`, `test_http_executor_sends_bearer_digest_and_stable_idempotency_key`, `test_http_executor_rejects_redirect_oversized_or_invalid_result`, `test_router_injects_configured_registry_into_each_control_service`, `test_health_is_503_when_enabled_executor_readiness_fails`, and `test_health_remains_200_when_control_gateway_is_disabled`. Assert the precise startup exception, outbound method/path/headers/body, closed result parsing, injected asset-type keys, and documented health status/body in each named test.

- [ ] **Step 2: Run RED**

```powershell
cd backend
python -m pytest tests/control/test_executors.py tests/control/test_startup.py tests/core/test_settings.py -q -p no:cacheprovider
```

Expected: FAIL because no registry, executor settings, readiness, or router injection exists.

- [ ] **Step 3: Implement strict configuration and HTTP transport**

Parse only the documented JSON keys, reject duplicate/blank asset types, read tokens through the existing `read_secret(token_env)` path, require HTTPS in production, and cap timeouts to `1..30` seconds. Build `httpx.Client(trust_env=False, follow_redirects=False)` with normal certificate verification. Send POST `${base_url}/commands/execute` or `/commands/rollback`, bearer authorization, `Idempotency-Key`, and a bounded JSON body containing command/project/policy digests, target, parameters or rollback plan, simulation digest, and validity end. Parse at most 64 KiB into the closed result model and retain only a bounded safe summary.

- [ ] **Step 4: Wire startup and readiness**

Construct one immutable default registry at application startup, inject `registry.executors` through `create_router(service_factory=control_service_factory)`, and call `registry.validate_startup()` from lifespan. When enabled, missing/invalid configuration fails startup. `GET /api/health` calls each configured `${base_url}/health` with a two-second timeout and returns HTTP 503 with `{"status":"not_ready","control":{"ready":false,"unavailable_asset_types":["reservoir"]}}` for a configured unavailable reservoir adapter; disabled control retains the existing 200 response.

- [ ] **Step 5: Run GREEN and commit**

```powershell
python -m pytest tests/control/test_executors.py tests/control/test_startup.py tests/core/test_settings.py -q -p no:cacheprovider
git add app/control/executors.py app/control/router.py app/core/settings.py app/main.py tests/control/test_executors.py tests/control/test_startup.py tests/core/test_settings.py
git commit -m "feat: wire control executor registry"
```

---

### Task 4: Atomic Audit, Stable Errors, And Complete Control API

**Files:**
- Create: `backend/app/control/errors.py`
- Modify: `backend/app/control/models.py`
- Modify: `backend/app/control/schemas.py`
- Modify: `backend/app/control/service.py`
- Modify: `backend/app/control/router.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/control/test_service.py`
- Test: `backend/tests/control/test_api.py`
- Test: `backend/tests/control/test_audit.py`

**Interfaces:**
- `ControlError(status_code, code, message, details={})`
- Error codes: `control_not_found`, `control_forbidden`, `control_idempotency_conflict`, `control_validation_failed`, `control_command_expired`, `control_executor_unavailable`, `control_invalid_state`, `control_execution_in_progress`.
- Error body: `{"code": str, "message": str, "details": dict[str, str]}`.
- `ControlPolicyUpdateRequest(parameter_schema?, max_command_lifetime_seconds?, approval_mode?, deviation_thresholds?, enabled?)`
- `ControlPolicyInfo`, `ControlCommandListItem`, and `ControlCommandDetail` where detail includes policy snapshot, approval count/decisions, attempts, feedback records, simulation attestation, deviation, and rollback provenance.
- `ControlService.get_policy`, `update_policy`, `list_policies(filters)`, `list_commands(filters)`, and `get_command_detail` remain project scoped.
- Public routes add `GET/PATCH /api/control/policies/{policy_id}` and filters `enabled`, `asset_type`, `command_type`, `status`, `policy_id`, `created_after`, and `created_before`.

- [ ] **Step 1: Write failing API and atomicity tests**

Add `test_policy_detail_patch_and_filters_are_project_scoped` and assert foreign scope is a safe 404. Add `test_command_filters_and_detail_return_approval_attempt_feedback_progress` and assert the response contains actual/required approval counts plus ordered attempts and feedback. Add `test_approve_body_cannot_contain_contradictory_decision` and assert an extra `decision` field is HTTP 422. Add `test_expired_execute_returns_control_command_expired_with_http_410`, `test_each_documented_control_error_has_stable_code_and_safe_message`, and `test_unexpected_database_error_is_logged_and_returns_500` with exact status/code assertions. Add `test_audit_failure_rolls_back_policy_proposal_approval_and_feedback_mutations`, `test_execution_start_result_failure_expiry_and_rollback_pending_are_audited`, and `test_completion_audit_failure_leaves_reconcilable_pending_attempt`; query both domain and audit rows from a new session to prove atomicity.

- [ ] **Step 2: Run RED**

```powershell
cd backend
python -m pytest tests/control/test_api.py tests/control/test_audit.py tests/control/test_service.py -q -p no:cacheprovider
```

- [ ] **Step 3: Make audit writes transactionally inseparable**

For policy create/update, proposal, approval/rejection, expiry, feedback, and rollback-state mutations, add the deterministic audit record before the single commit. Use keys derived from operation identity, for example `control:{command_id}:approve:{approver_id}` and `control:{command_id}:execute:{attempt_id}:completed`; remove random UUID audit keys. For external execution, phase one atomically commits pending attempt + state + `execution_started`; phase two atomically commits attempt result + state + `execution_completed` or `execution_unknown`. If phase-two audit fails, rollback phase two so same-key reconciliation remains possible.

- [ ] **Step 4: Implement typed errors and complete endpoints**

Follow `RunnerGatewayError`'s established handler pattern. Remove broad `except Exception` blocks from the router; domain errors go through the registered handler, while unexpected exceptions are logged with a trace and remain HTTP 500. Return 410 after atomically marking/auditing expiry. Replace `ApprovalRequest` with a reason-only body so path and body cannot contradict.

Validate policy JSON Schema with `Draft202012Validator.check_schema` on create and patch. Store a canonical policy digest and threshold snapshot on proposal so later policy edits affect only future commands.

- [ ] **Step 5: Run GREEN and commit**

```powershell
python -m pytest tests/control/test_api.py tests/control/test_audit.py tests/control/test_service.py -q -p no:cacheprovider
git add app/control app/main.py tests/control
git commit -m "fix: complete audited control API"
```

---

### Task 5: Complete Permission-Gated Operations UI

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/control.ts`
- Modify: `frontend/src/views/control/ControlCommandView.vue`
- Create: `frontend/src/views/control/components/ControlPolicyPanel.vue`
- Create: `frontend/src/views/control/components/ControlCommandProposalModal.vue`
- Create: `frontend/src/views/control/components/ControlCommandDetailDrawer.vue`
- Create: `frontend/src/views/control/components/ControlFeedbackModal.vue`
- Create: `frontend/src/views/control/controlActionKeys.ts`
- Modify: `frontend/src/stores/permission.test.ts`
- Test: `frontend/src/api/control.test.ts`
- Test: `frontend/src/views/control/ControlCommandView.test.ts`
- Test: `frontend/src/views/control/controlActionKeys.test.ts`

**Interfaces:**
- `ApiError(message, status, code?, details?)` decodes both the new control envelope and legacy `detail` responses.
- `controlApi.listPolicies(filters)`, `getPolicy`, `createPolicy`, `updatePolicy`, `listCommands(filters)`, `getCommand`, `propose`, `approve`, `reject`, `execute`, `feedback`, and `rollback` mirror Task 4.
- `controlActionKeys.keyFor(commandId, kind)` returns the same UUID until a terminal HTTP response, and `clear(commandId, kind)` runs only after a confirmed response.

- [ ] **Step 1: Write failing API, rendering, permission, and action tests**

Add tests named `decodes structured control error codes while preserving legacy detail errors`, `lists and filters policies and commands with encoded query values`, `shows simulation approval execution feedback deviation and rollback detail`, `shows policy and proposal controls only with manage and propose permissions`, `shows feedback only in awaiting_feedback and abnormal reconciliation states`, `confirms execute and rollback and reuses the action idempotency key after timeout`, `renders stable safe error messages and preserves current list data on action failure`, and `renders a single-column non-overlapping workflow at 390px width`. Assert exact request URLs/bodies/headers, permission-specific button absence, drawer field text, confirmation behavior, stable UUID reuse, retained rows, and measured element bounds without overlap.

- [ ] **Step 2: Run RED**

```powershell
cd frontend
npm test -- src/api/control.test.ts src/views/control/ControlCommandView.test.ts src/views/control/controlActionKeys.test.ts src/stores/permission.test.ts
```

- [ ] **Step 3: Implement the typed API and workflow UI**

Use a restrained full-width operations layout: command filters and table as the primary surface, an adjacent unframed policy tab/panel, modals for policy/proposal/feedback forms, and a drawer for immutable evidence and history. Show actual `approval_count / required_approvals`, attestation digest, validity, latest attempt/outcome, external reference, observed time, deviations, and rollback attempts. Use Ant Design icons and tooltips for icon-only refresh/detail actions.

Gate approve/reject, execute, feedback, rollback, proposal, and policy management independently with the existing `control:*` permissions. Disable row actions while one is pending. Require explicit confirmation for execute and physical rollback. Preserve the same execute/rollback UUID after timeout or network loss; clear it only after an HTTP response proves the operation was accepted, replayed, or rejected as a terminal conflict.

- [ ] **Step 4: Run GREEN, type-check, and commit**

```powershell
npm test -- src/api/control.test.ts src/views/control/ControlCommandView.test.ts src/views/control/controlActionKeys.test.ts src/stores/permission.test.ts src/router/routes.test.ts
npm run build
git add src/api/client.ts src/api/control.ts src/views/control src/stores/permission.test.ts
git commit -m "feat: complete control operations console"
```

Expected: all tests PASS, `vue-tsc --noEmit` passes through `npm run build`, and unauthorized controls are absent rather than merely disabled.

---

### Task 6: PostgreSQL Migration Gates, Real Team-Control E2E, And Release Documentation

**Files:**
- Modify: `backend/alembic/versions/20260913_28_control_commands.py`
- Verify: `backend/alembic/versions/20260914_30_merge_control_skill_heads.py`
- Verify: `backend/alembic/versions/20260915_31_control_execution_hardening.py`
- Create: `backend/tests/integration/test_control_migrations_postgres.py`
- Modify: `backend/tests/integration/test_postgres_migrations.py`
- Modify: `tests/e2e/test_published_team_run.py`
- Modify: `backend/README.md`
- Modify: `frontend/README.md`
- Modify: `.env.example`

**Interfaces:**
- Alembic has one head: `20260915_31`.
- Upgrade paths tested: `20260908_27 -> 20260915_31` and `20260914_29 -> 20260915_31`.
- Legacy invalid strategy preflight query: `SELECT id, failure_strategy FROM collaboration_team_versions WHERE failure_strategy NOT IN ('fail_fast','continue_then_synthesize')`.
- The real e2e uses published Team snapshot/runtime artifact creation and attestation, dual human approval, configured HTTP executor replay, feedback deviation, and rollback attempt provenance.

- [ ] **Step 1: Write failing PostgreSQL migration tests**

Add `test_control_upgrade_from_project_skill_head_creates_all_constraints` and assert every named control table, CHECK, FK, index, and unique constraint. Add `test_control_upgrade_from_skill_availability_head_reaches_single_head` and assert Alembic version `20260915_31`. Add `test_control_migration_aborts_with_actionable_error_for_invalid_legacy_failure_strategy` and assert the exception contains the row count and preflight query. Add `test_control_execution_hardening_backfills_and_downgrades_without_data_loss` and assert evaluated/unevaluated success backfills plus downgrade restoration. Add `test_postgres_enforces_kind_scoped_attempt_uniqueness_and_closed_states` and assert same key works across kinds but duplicates within a kind and unknown status/outcome strings fail.

Use a dedicated `TEST_DATABASE_URL`, migrate to the named starting revision, insert representative pre-existing rows, run Alembic as a subprocess, inspect named constraints/indexes/tables, and restore the database in `finally`. Do not run against the service or developer database.

- [ ] **Step 2: Run PostgreSQL tests and verify RED**

```powershell
cd backend
python -m pytest tests/integration/test_control_migrations_postgres.py tests/integration/test_postgres_migrations.py -q -p no:cacheprovider
```

Expected: FAIL until revision 31 and the legacy preflight are complete; SKIP only when `TEST_DATABASE_URL` is intentionally absent.

- [ ] **Step 3: Make migration 28 fail safely on unsupported legacy data**

Before adding the CHECK, query the invalid rows. If any exist, raise an actionable migration error containing the row count and the exact preflight SQL, without silently rewriting published Team snapshots or their digests. Document the maintenance decision: inspect each invalid row, migrate drafts through the Team service, and require explicit operator approval before mapping any published invalid value to conservative `fail_fast`. Keep merge revision 30 unchanged and revision 31 as the sole head.

- [ ] **Step 4: Add the real Task 5 integration scenario**

Add `test_team_simulation_dual_approval_execute_feedback_deviation_and_rollback` to `tests/e2e/test_published_team_run.py`. It must use a real SQLite database and real `TeamService`, `SandboxRuntime`, runner gateway/artifact services, `ControlService`, and audit recorder. Only the physical endpoint is fake: inject `httpx.MockTransport` through `ControlExecutorRegistry`. Assert:

```python
assert artifact.provenance["simulation_attestation"]["status"] == "succeeded"
assert command.team_version_id == published_version.id
assert command.status == "pending_approval"          # after first of two approvals
assert command.status == "approved"                  # after distinct second approver
assert executed.status == "awaiting_feedback"
assert executor.logical_execute_count == 1            # including same-key replay
assert feedback.status == "deviation"
assert rollback.status == "rolled_back"
assert [a.kind for a in attempts] == ["execute", "rollback"]
assert all_required_audit_actions_are_present(session, command.id)
```

Also assert requester self-approval fails, a foreign project sees 404, stale feedback fails, and rollback replay does not call the endpoint twice.

- [ ] **Step 5: Document deployment and compatibility**

Document the exact executor JSON shape, secret environment names, HTTPS requirement, `/health` contract, external execute/rollback payload and closed response schema, stable API error envelope, migration preflight/remediation, `awaiting_feedback` semantics, and same-key reconciliation procedure. State that the control API was unreleased, so `team_version -> team_version_id` is not a public compatibility break; generic artifacts and existing Team APIs remain unchanged.

- [ ] **Step 6: Run the complete release gate**

```powershell
cd backend
python -m pytest tests/control tests/runtime/test_artifact_backend.py tests/runtime/test_runner_gateway_api.py tests/runtime/test_runner_gateway_state.py tests/runtime/test_sandbox_runtime.py tests/collaboration tests/identity/test_bootstrap.py -q -p no:cacheprovider
cd ..
python -m pytest tests/e2e/test_published_team_run.py backend/tests/integration/test_control_migrations_postgres.py backend/tests/integration/test_postgres_migrations.py -q -p no:cacheprovider
cd frontend
npm test
npm run build
cd ..
git diff --check 1737e33e3f5a6eace0b5e53e338b6ec3f81e5c23...HEAD
git status --short
```

Expected: all non-environment-gated tests PASS; PostgreSQL tests PASS when `TEST_DATABASE_URL` is supplied; frontend tests and build PASS; diff check is empty; status contains only intended remediation files.

- [ ] **Step 7: Commit the release gate**

```powershell
git add backend/alembic/versions/20260913_28_control_commands.py backend/alembic/versions/20260915_31_control_execution_hardening.py backend/tests/integration/test_control_migrations_postgres.py backend/tests/integration/test_postgres_migrations.py tests/e2e/test_published_team_run.py backend/README.md frontend/README.md .env.example
git commit -m "test: verify production control workflow"
```

## Review Coverage Checklist

- Critical 1, impossible Team simulation provenance and missing Task 5 integration: Tasks 1 and 6.
- Critical 2, no production executor injection: Task 3.
- Critical 3, physical side effect without durable attempt and unchecked outcomes: Task 2.
- Critical 4, premature terminal success: Task 2.
- Important 1, non-atomic or missing audit events: Task 4.
- Important 2, incomplete API/UI/details/filtering: Tasks 4 and 5.
- Important 3, rollback replay and cross-kind request-key collision: Task 2.
- Important 4, unstable error strings, 200 expiry, and unexpected 409 mapping: Task 4.
- Important 5, inadequate migration and legacy-data coverage: Task 6.
- Important 6, contradictory ignored approval decision: Task 4.
- Safety-relevant Minor 1, discarded/stale `observed_at`: Task 2.
- Safety-relevant Minor 2, ambiguous unvalidated Team version: Tasks 1 and 2.
