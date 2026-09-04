# Final Review Fix 3 Report

## Status

DONE. The final-review findings are remediated, including the required live
coordinator -> HTTP Runner -> launcher -> child worker -> HTTP Gateway ->
PostgreSQL deadline test. No blocker remains.

## Investigation

- Approval resume did not load the immutable Team snapshot before approved Tool
  execution, so a resumed external Tool could start after the Team deadline.
- The global-default Agent route treated `unit_admin` as platform authority.
- Team fail-fast polling waited in declaration order, and an approval
  interruption stopped collection before all parallel approval records were
  persisted.
- Timeout teardown derived its control allowance from the expired watchdog and
  repeated token revocation before cleanup.
- Completion could start artifact validation before the Team deadline and
  commit success after it because the Gateway did not recheck the immutable
  deadline immediately before completion side effects.
- Near the deadline, Runner or Launcher request enforcement could reject a
  request before the coordinator's next clock comparison. Both HTTP clients
  collapsed deadline expiry into a generic availability error, causing the
  coordinator to persist `launcher_unavailable` instead of `sandbox_timeout`.

## RED / GREEN Evidence

All commands below were run from the worktree root. Historical durations are
reported only where they were retained. The two inherited completion-deadline
cycles are included because this fix wave builds directly on them.

### Inherited local Team completion deadline

RED:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend/tests/runtime/test_sandbox_runtime.py::test_team_success_expiring_during_final_artifact_collection_reports_timeout -q --basetemp backend/.tmp-final-review-fix3-late-success-local-red-20260904-a
```

Result: `1 failed`; the actual status was `completed` while the test expected
`failed`. The historical duration was not retained.

GREEN:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend/tests/runtime/test_sandbox_runtime.py::test_team_success_expiring_during_final_artifact_collection_reports_timeout -q --basetemp backend/.tmp-final-review-fix3-late-success-local-green-20260904-a
```

Result: `1 passed`. The historical duration was not retained.

### Inherited locked Gateway completion boundary

RED:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend/tests/runtime/test_runner_gateway_state.py::test_expired_team_success_is_rejected_at_locked_gateway_boundary -q --basetemp backend/.tmp-final-review-fix3-late-success-gateway-red-20260904-a
```

Result: `1 failed`; HTTP 200 was returned where HTTP 409 was expected. The
historical duration was not retained.

GREEN: the exact historical command line and basetemp were not retained. The
retained selection covered
`test_completion_commits_final_message_status_and_artifact_references_once`
and `test_expired_team_success_is_rejected_at_locked_gateway_boundary`.

Result: `2 passed`, covering the new Team rejection and existing Agent
completion behavior. The historical duration was not retained.

### Approval preflight

RED:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend/tests/conversations/test_dispatcher.py::test_expired_team_approval_resume_stops_before_external_tool -q --basetemp backend/.tmp-final-review-fix3-approval-preflight-red-rerun-20260904-a
```

Result: `1 failed, 2 warnings in 2.55s`; `_execute_approved_tool(...)` returned
an invocation id instead of `None`.

GREEN:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend/tests/conversations/test_dispatcher.py::test_expired_team_approval_resume_stops_before_external_tool -q --basetemp backend/.tmp-final-review-fix3-approval-preflight-green-rerun-20260904-a
```

Result: `1 passed, 1 warning in 2.56s`.

### Tool execution crossing the Team deadline

RED:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend/tests/tools/test_gateway.py::test_approved_team_tool_crossing_execution_deadline_commits_no_success -q --basetemp backend/.tmp-final-review-fix3-tool-cross-red-20260904-b
```

Result: failed with `DID NOT RAISE ToolRuntimeError`; the historical duration
was not retained.

GREEN:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend/tests/tools/test_gateway.py::test_approved_team_tool_crossing_execution_deadline_commits_no_success -q --basetemp backend/.tmp-final-review-fix3-tool-cross-green-20260904-b
```

Result: `1 passed in 2.11s`.

### Platform-global Agent default

RED:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend/tests/test_agents.py::test_unit_admin_cannot_mutate_platform_default_pointer -q --basetemp backend/.tmp-final-review-fix3-agent-default-red-20260904-a
```

Result: `1 failed, 2 warnings in 3.27s`; the unit administrator received HTTP
200 where HTTP 403 was expected.

GREEN:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend/tests/test_agents.py::test_project_admin_cannot_mutate_platform_default_pointer backend/tests/test_agents.py::test_unit_admin_cannot_mutate_platform_default_pointer backend/tests/test_agents.py::test_unit_admin_cannot_validate_or_mutate_platform_default_target -q --basetemp backend/.tmp-final-review-fix3-agent-default-green-rerun-20260904-a
```

Result: `4 passed, 1 warning in 6.56s`.

### Immediate fail-fast observation

RED:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend/tests/runtime/test_sandbox_runtime.py::test_team_fail_fast_does_not_wait_for_blocked_sibling_or_persist_its_result -q --basetemp backend/.tmp-final-review-fix3-failfast-red-20260904-a
```

Result: `1 failed, 2 warnings in 4.58s`; elapsed time was about 0.75 seconds,
above the required 0.5-second bound.

GREEN:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend/tests/runtime/test_sandbox_runtime.py::test_team_fail_fast_does_not_wait_for_blocked_sibling_or_persist_its_result backend/tests/runtime/test_sandbox_runtime.py::test_team_fail_fast_failure_wins_over_parallel_approval_interruption -q --basetemp backend/.tmp-final-review-fix3-failfast-green-20260904-a
```

Result: `2 passed, 1 warning in 3.64s`.

The later strengthened version of the late-checkpoint assertion had no
separately captured RED run. Its retained GREEN was:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend/tests/runtime/test_sandbox_runtime.py::test_team_fail_fast_does_not_wait_for_blocked_sibling_or_persist_its_result -q --basetemp backend/.tmp-final-review-fix3-failfast-fence-green-20260904-a
```

Result: `1 passed, 1 warning in 5.32s`.

### Timeout teardown allowance

RED:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend/tests/runtime/test_run_lifecycle.py::test_timeout_teardown_gets_live_control_allowance_after_slow_revocation -q --basetemp backend/.tmp-final-review-fix3-timeout-teardown-red-20260904-a
```

Result: `1 failed, 2 warnings in 2.65s`; termination received an expired
deadline and cleanup received `None`.

GREEN:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend/tests/runtime/test_run_lifecycle.py::test_timeout_teardown_gets_live_control_allowance_after_slow_revocation backend/tests/runtime/test_run_lifecycle.py::test_timeout_terminate_and_cleanup_share_one_control_allowance -q --basetemp backend/.tmp-final-review-fix3-timeout-teardown-duplicate-green-20260904-a
```

Result: `2 passed, 1 warning in 4.70s`.

### Typed Launcher and Runner deadline identity

RED:

```powershell
$env:PYTHONPATH='backend'
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/runtime/test_launcher_api.py backend/tests/runtime/test_launcher_client.py backend/tests/runtime/test_workflow_runner_api.py backend/tests/runtime/test_workflow_runner.py -k "deadline_expiry_identity or rejects_expired" --basetemp=backend/.tmp-final-review-fix3-deadline-identity-red-20260904-a
```

Result: `10 failed, 28 deselected in 1.19s`.

GREEN:

```powershell
$env:PYTHONPATH='backend'
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/runtime/test_launcher_api.py backend/tests/runtime/test_launcher_client.py backend/tests/runtime/test_workflow_runner_api.py backend/tests/runtime/test_workflow_runner.py -k "deadline_expiry_identity or rejects_expired" --basetemp=backend/.tmp-final-review-fix3-deadline-identity-green-20260904-a
```

Result: `10 passed, 28 deselected in 0.73s`.

Transport timeout RED:

```powershell
$env:PYTHONPATH='backend'
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/runtime/test_launcher_client.py backend/tests/runtime/test_workflow_runner.py -k "deadline_expiry_identity or launcher_prepare_translates_transport_deadline_expiry" --basetemp=backend/.tmp-final-review-fix3-httpx-timeout-red-20260904-a
```

Result: `4 failed, 8 passed, 15 deselected in 0.46s`.

GREEN:

```powershell
$env:PYTHONPATH='backend'
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/runtime/test_launcher_client.py backend/tests/runtime/test_workflow_runner.py -k "deadline_expiry_identity or launcher_prepare_translates_transport_deadline_expiry" --basetemp=backend/.tmp-final-review-fix3-httpx-timeout-green-20260904-a
```

Result: `12 passed, 15 deselected in 0.21s`.

### Coordinator execute and recovery deadline classification

RED:

```powershell
$env:PYTHONPATH='backend'
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/runtime/test_launcher_client.py backend/tests/runtime/test_workflow_runner_api.py backend/tests/runtime/test_workflow_runner.py backend/tests/runtime/test_run_lifecycle.py -k "preserves_deadline_expiry_from_inspection or propagates_launcher_deadline_expiry or preserves_deadline_expiry_from_health_check or typed_runner_deadline" --basetemp=backend/.tmp-final-review-fix3-deadline-propagation-red-20260904-b
```

Result: `6 failed, 68 deselected in 4.68s`.

GREEN:

```powershell
$env:PYTHONPATH='backend'
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/runtime/test_launcher_client.py backend/tests/runtime/test_workflow_runner_api.py backend/tests/runtime/test_workflow_runner.py backend/tests/runtime/test_run_lifecycle.py -k "preserves_deadline_expiry_from_inspection or propagates_launcher_deadline_expiry or preserves_deadline_expiry_from_health_check or typed_runner_deadline" --basetemp=backend/.tmp-final-review-fix3-deadline-propagation-green-20260904-a
```

Result: `6 passed, 68 deselected in 4.09s`.

Dual-clock execute/recovery RED:

```powershell
$env:PYTHONPATH='backend'
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/runtime/test_run_lifecycle.py -k "runner_error_after_execution_wall_deadline" --basetemp=backend/.tmp-final-review-fix3-wall-clock-red-20260904-a
```

Result: `2 failed, 40 deselected in 4.08s`; both cases persisted
`launcher_unavailable` instead of `sandbox_timeout`.

GREEN:

```powershell
$env:PYTHONPATH='backend'
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/runtime/test_run_lifecycle.py -k "runner_error_after_execution_wall_deadline" --basetemp=backend/.tmp-final-review-fix3-wall-clock-green-20260904-a
```

Result: `2 passed, 40 deselected in 3.43s`.

Neighbor classification verification:

```powershell
$env:PYTHONPATH='backend'
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/runtime/test_run_lifecycle.py -k "launcher_outage or watchdog_expires or runner_failure_after_team_deadline or runner_error_after_execution_wall_deadline" --basetemp=backend/.tmp-final-review-fix3-wall-clock-neighbors-20260904-a
```

Result: `8 passed, 34 deselected in 14.39s`.

### Live PostgreSQL post-artifact completion fence

The post-artifact Gateway fence has no separate unit RED/GREEN pair. Its
meaningful RED is the required live boundary test:

```powershell
$env:PATH = "I:\智能体平台\IntelligentAgentPlatform\.worktrees\published-team-foundation\.testvenv-task5\Scripts;$env:PATH"
& .\backend\tests\support\run_postgres_tests.ps1 -PytestPath backend/tests/integration/test_runtime_deadline_e2e.py
```

Result: `1 failed in 13.11s`; artifact validation started before and finished
after the Team deadline, the child exited 0, PostgreSQL contained `completed`
instead of `failed`, and the wrapper raised `PostgreSQL test run failed`.

After the completion fence, the seven-test aggregate initially returned
`1 failed, 6 passed in 15.31s`: child exit 4 and failed Run state were correct,
but the terminal reason remained `launcher_unavailable`. Typed deadline
propagation corrected that final classification.

Final GREEN aggregate command:

```powershell
$env:PATH = "I:\智能体平台\IntelligentAgentPlatform\.worktrees\published-team-foundation\.testvenv-task5\Scripts;$env:PATH"
& .\backend\tests\support\run_postgres_tests.ps1 -PytestPath @(
  'backend/tests/integration/test_runtime_deadline_e2e.py',
  'backend/tests/integration/test_runner_gateway_state_concurrency.py'
)
```

Results: `7 passed in 16.64s`, `7 passed in 16.14s`, and final
`7 passed in 15.99s`, all with zero skips. The final E2E state was
`failed`/`sandbox_timeout`, child exit 4, with no successful
`runner.completion` event. A separate final single-E2E GREEN duration was not
retained.

## Changes

### Production files

- `backend/app/agents/router.py`
- `backend/app/conversations/dispatcher.py`
- `backend/app/runtime/execution_snapshot.py`
- `backend/app/runtime/launcher_api.py`
- `backend/app/runtime/launcher_client.py`
- `backend/app/runtime/run_lifecycle.py`
- `backend/app/runtime/runner_gateway_service.py`
- `backend/app/runtime/sandbox_runtime.py`
- `backend/app/runtime/workflow_runner.py`
- `backend/app/runtime/workflow_runner_api.py`
- `backend/app/tools/gateway.py`

These changes enforce approval preflight, immutable Team deadlines, locked
completion and post-Tool fences, prompt fail-fast observation, bounded teardown,
and typed downstream deadline propagation while preserving generic outage
classification.

### Test files

- `backend/tests/conversations/test_dispatcher.py`
- `backend/tests/integration/test_runtime_deadline_e2e.py` (new)
- `backend/tests/runtime/test_launcher_api.py`
- `backend/tests/runtime/test_launcher_client.py`
- `backend/tests/runtime/test_run_lifecycle.py`
- `backend/tests/runtime/test_runner_gateway_state.py`
- `backend/tests/runtime/test_sandbox_runtime.py`
- `backend/tests/runtime/test_workflow_runner.py`
- `backend/tests/runtime/test_workflow_runner_api.py`
- `backend/tests/test_agents.py`
- `backend/tests/tools/test_gateway.py`

### Report file

- `.superpowers/sdd/2026-09-01-published-team-review-remediation/final-review-fix3-report.md`

This is the complete initial `75da991` commit scope: 11 production files,
11 test files, and this report. Virtual environments, downloaded dependencies,
basetemps, logs, XML, build output, and all other pre-existing untracked
artifacts were excluded. Later fix rounds expand the final committed union
enumerated at the end of this report.

## Verification

- Newly touched Launcher/Runner/coordinator modules:
  `94 passed in 69.71s`.
- Modified-surface aggregate covering Agent, conversation, lifecycle, Runner
  state, sandbox runtime, and Tool Gateway:
  `230 passed, 2 skipped in 274.58s`.
- PostgreSQL E2E and concurrency aggregate passed three times with no skips:
  `7 passed in 16.64s`, `7 passed in 16.14s`, and final
  `7 passed in 15.99s`.
- Focused fix-wave aggregate:

  ```powershell
  .\.testvenv-task5\Scripts\python.exe -m pytest backend/tests/conversations/test_dispatcher.py::test_expired_team_approval_resume_stops_before_external_tool backend/tests/test_agents.py::test_unit_admin_cannot_mutate_platform_default_pointer backend/tests/runtime/test_sandbox_runtime.py::test_team_fail_fast_does_not_wait_for_blocked_sibling_or_persist_its_result backend/tests/runtime/test_run_lifecycle.py::test_timeout_teardown_gets_live_control_allowance_after_slow_revocation -q --basetemp backend/.tmp-final-review-fix3-final-focused-20260904-a
  ```

  Result: `4 passed, 1 warning in 14.12s`.
- The final E2E persisted Run status is `failed` with `sandbox_timeout`; no
  successful `runner.completion` event was committed. The disposable
  PostgreSQL wrapper exited successfully and removed its container; subsequent
  inspection confirmed no disposable container remained.

## Residual Risks

- The strongest late-checkpoint fail-fast test has retained GREEN evidence but
  no separate RED capture after its assertion was strengthened.
- A separate final single-test E2E GREEN duration was not retained; the same E2E
  passed in all three recorded seven-test PostgreSQL aggregate runs.
- The report-only amendment does not alter any production or test blob from the
  verified fix commit.

## Self-review

The deadline classification uses typed exceptions and HTTP 504 rather than
message parsing or timing tolerance. Generic HTTP 503, connection, and mid-run
outage behavior remains `launcher_unavailable`. The E2E's temporary diagnostic
tuple was removed after stabilization. The complete `1365492..HEAD` scope was
enumerated above, and no temporary artifact is staged.

## Fix Round 1

### Investigation and authority decision

- Generic `TimeoutError` and `httpx.TimeoutException` values were classified as
  deadline expiry whenever a deadline argument existed, even when that deadline
  was far in the future. The exception type alone cannot distinguish an
  exhausted end-to-end budget from a connection outage.
- Team fail-fast stopped waiting for siblings but only fenced their checkpoint
  callbacks. A released sibling could still reach its model or Tool transport
  after the scheduler had observed another member's failure.
- Team completion and Tool terminal persistence checked the immutable deadline
  before writes but did not flush and recheck before commit. A deadline crossed
  during those writes could therefore commit a late success.
- `RunnerGatewayService._map_tool_error` collapsed the resulting typed Tool
  `sandbox_timeout` into HTTP 502 instead of the established HTTP 409 conflict.
- The platform-default pointer is global and has no supported platform HTTP
  principal. All authenticated HTTP callers therefore remain denied. The
  established system authority is the internal context-free call
  `AgentService.set_default(..., context=None)`; no role header or unbindable
  platform role was invented.

### RED evidence

Core RED command:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/runtime/test_launcher_client.py::test_launcher_client_keeps_far_future_connect_timeout_as_unavailable backend/tests/runtime/test_workflow_runner.py::test_runner_client_keeps_far_future_connect_timeout_as_unavailable backend/tests/runtime/test_sandbox_runtime.py::test_team_fail_fast_fences_blocked_sibling_before_external_tool_invocation backend/tests/runtime/test_runner_gateway_state.py::test_team_success_crossing_deadline_during_completion_writes_rolls_back backend/tests/integration/test_runner_gateway_execution.py::test_team_tool_deadline_error_is_returned_as_conflict --basetemp=backend/.tmp-final-review-fix3-round1-red-core-20260904-a
```

Result: `5 failed in 4.32s`. The two clients returned deadline subclasses for
far-future connection timeouts, one late Tool call reached the external
transport, late completion returned HTTP 200, and Tool timeout mapped to HTTP
502.

Deep RED command:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/runtime/test_sandbox_runtime.py::test_team_fail_fast_fences_blocked_sibling_before_external_model_invocation backend/tests/tools/test_gateway.py::test_team_tool_terminal_flush_crossing_deadline_commits_no_success --basetemp=backend/.tmp-final-review-fix3-round1-red-deep-20260904-a
```

Result: `2 failed in 8.17s`. The released sibling reached the external model
transport, and Tool completion did not raise after its terminal flush crossed
the deadline.

Live PostgreSQL RED command:

```powershell
$env:PATH = "I:\智能体平台\IntelligentAgentPlatform\.worktrees\published-team-foundation\.testvenv-task5\Scripts;$env:PATH"
& .\backend\tests\support\run_postgres_tests.ps1 -PytestPath 'backend/tests/integration/test_runner_gateway_state_concurrency.py::test_team_completion_rechecks_deadline_after_blocked_post_write_flush'
```

Result: `1 failed in 3.38s`, zero skips. Team success committed after the
completion flush was blocked across the immutable deadline.

Authorization characterization command:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_agents.py::test_unit_admin_cannot_mutate_platform_default_pointer backend/tests/test_agents.py::test_internal_system_authority_mutates_platform_default_for_all_units --basetemp=backend/.tmp-final-review-fix3-round1-auth-characterization-20260904-a
```

Result: `2 passed in 5.34s`. This confirms existing internal authority and
cross-unit visibility; it required no production authorization change.

### Implementation

- Launcher and Workflow Runner clients now classify generic timeout exceptions
  as deadline expiry only after the corresponding wall or monotonic clock has
  reached the supplied deadline. Typed deadline errors and HTTP 504 responses
  retain their deadline identity.
- Each schema-v5 Team member batch passes its shared cancellation `Event` into
  `GatewayChatModel` and its Tool wrappers. Both check that event immediately
  before the external transport call. Schema-v4 keeps its required team-wide
  model/Tool object identity and uses a shared legacy cancellation event.
- Successful Team completion flushes all messages, events, status, and
  idempotency writes, rechecks the immutable Team deadline, and rolls back with
  HTTP 409 `sandbox_timeout` before commit when the deadline was crossed.
- Successful Tool terminal persistence likewise flushes and rechecks, rolling
  back to the already-committed `started` record and raising
  `ToolRuntimeError("sandbox_timeout", ...)` instead of committing success.
- The Runner Gateway maps Tool `sandbox_timeout` to HTTP 409.

### GREEN evidence

Exact core rerun:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/runtime/test_launcher_client.py::test_launcher_client_keeps_far_future_connect_timeout_as_unavailable backend/tests/runtime/test_workflow_runner.py::test_runner_client_keeps_far_future_connect_timeout_as_unavailable backend/tests/runtime/test_sandbox_runtime.py::test_team_fail_fast_fences_blocked_sibling_before_external_tool_invocation backend/tests/runtime/test_runner_gateway_state.py::test_team_success_crossing_deadline_during_completion_writes_rolls_back backend/tests/integration/test_runner_gateway_execution.py::test_team_tool_deadline_error_is_returned_as_conflict --basetemp=backend/.tmp-final-review-fix3-round1-green-core-20260904-a
```

Result: `5 passed in 2.99s`.

Deep fail-fast and Tool flush rerun:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/runtime/test_sandbox_runtime.py::test_team_fail_fast_fences_blocked_sibling_before_external_model_invocation backend/tests/tools/test_gateway.py::test_team_tool_terminal_flush_crossing_deadline_commits_no_success --basetemp=backend/.tmp-final-review-fix3-round1-green-deep-20260904-b
```

Result: `2 passed in 4.54s`.

The first affected aggregate exposed four stale Launcher fixtures that raised a
generic timeout before their future deadline and one schema-v4 wrapper-identity
regression: `5 failed, 240 passed, 9 skipped in 183.51s`. The fixtures now
advance the wall clock to the transport deadline before raising, and the legacy
Team path retains shared wrapper identity. Targeted compatibility rerun:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/runtime/test_launcher_client.py::test_launcher_prepare_translates_transport_deadline_expiry backend/tests/runtime/test_sandbox_runtime.py::test_schema_v4_team_runtime_preserves_legacy_team_wide_construction backend/tests/runtime/test_sandbox_runtime.py::test_team_fail_fast_fences_blocked_sibling_before_external_tool_invocation backend/tests/runtime/test_sandbox_runtime.py::test_team_fail_fast_fences_blocked_sibling_before_external_model_invocation --basetemp=backend/.tmp-final-review-fix3-round1-regression-green-20260904-a
```

Result: `9 passed in 3.07s`.

Affected aggregate command:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider backend/tests/runtime/test_launcher_client.py backend/tests/runtime/test_workflow_runner.py backend/tests/runtime/test_gateway_model.py backend/tests/runtime/test_gateway_tools.py backend/tests/runtime/test_sandbox_runtime.py backend/tests/runtime/test_runner_gateway_state.py backend/tests/integration/test_runner_gateway_execution.py backend/tests/tools/test_gateway.py backend/tests/test_agents.py --basetemp=backend/.tmp-final-review-fix3-round1-affected-green-20260904-b
```

Result: `245 passed, 2 skipped in 180.34s`. Both skips are expected Windows
capability skips: file symlink creation and directory symlink creation are
unavailable. No warning class was emitted by this aggregate.

Focused live PostgreSQL GREEN command:

```powershell
$env:PATH = "I:\智能体平台\IntelligentAgentPlatform\.worktrees\published-team-foundation\.testvenv-task5\Scripts;$env:PATH"
& .\backend\tests\support\run_postgres_tests.ps1 -PytestPath 'backend/tests/integration/test_runner_gateway_state_concurrency.py::test_team_completion_rechecks_deadline_after_blocked_post_write_flush'
```

Result: `1 passed in 3.27s`, zero skips.

Full live PostgreSQL concurrency/deadline module:

```powershell
$env:PATH = "I:\智能体平台\IntelligentAgentPlatform\.worktrees\published-team-foundation\.testvenv-task5\Scripts;$env:PATH"
& .\backend\tests\support\run_postgres_tests.ps1 -PytestPath 'backend/tests/integration/test_runner_gateway_state_concurrency.py'
```

Result: `7 passed in 3.96s`, zero skips. The disposable wrapper exited
successfully and removed its container.

Final whitespace verification command:

```powershell
git diff --check
```

Result: exit code `0`. Git emitted only the repository's existing LF-to-CRLF
working-copy conversion notices; it reported no whitespace errors.

### Independent review correction

Independent review found one Important schema-v4 race in the first GREEN
implementation: legacy shared wrappers watched a second event set immediately
after the batch event. A sibling could pass its legacy event check in that
interval. Deterministic schema-v4 variants release the sibling inside that gap.

Legacy race RED command:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/runtime/test_sandbox_runtime.py::test_team_fail_fast_fences_blocked_sibling_before_external_tool_invocation backend/tests/runtime/test_sandbox_runtime.py::test_team_fail_fast_fences_blocked_sibling_before_external_model_invocation --basetemp=backend/.tmp-final-review-fix3-round1-legacy-race-red-20260904-a
```

Result: `2 failed, 2 passed in 3.81s`; both schema-v4 siblings reached their
external transports while both schema-v5 cases remained fenced.

The correction replaces the second event with a stable legacy reference that
is rebound to the actual per-batch event before worker threads launch. Model
and Tool object identity remains team-wide for schema v4, while cancellation
now requires only the single batch-event `set()`.

Legacy race GREEN command:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/runtime/test_sandbox_runtime.py::test_team_fail_fast_fences_blocked_sibling_before_external_tool_invocation backend/tests/runtime/test_sandbox_runtime.py::test_team_fail_fast_fences_blocked_sibling_before_external_model_invocation backend/tests/runtime/test_sandbox_runtime.py::test_schema_v4_team_runtime_preserves_legacy_team_wide_construction --basetemp=backend/.tmp-final-review-fix3-round1-legacy-race-green-20260904-a
```

Result: `5 passed in 2.94s`. Scoped re-review marked the Important finding
`ADDRESSED` and found no new Critical or Important breakage.

Final post-review affected aggregate:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider backend/tests/runtime/test_launcher_client.py backend/tests/runtime/test_workflow_runner.py backend/tests/runtime/test_gateway_model.py backend/tests/runtime/test_gateway_tools.py backend/tests/runtime/test_sandbox_runtime.py backend/tests/runtime/test_runner_gateway_state.py backend/tests/integration/test_runner_gateway_execution.py backend/tests/tools/test_gateway.py backend/tests/test_agents.py --basetemp=backend/.tmp-final-review-fix3-round1-affected-green-20260904-c
```

Result: `247 passed, 2 skipped in 219.31s`, zero failures. The only skips are
the same Windows file-symlink and directory-symlink capability skips.

## Fix Round 2

### Investigation

- Round 1 flushed every successful completion write and then rechecked the
  application clock, but `Session.commit()` remained a separate operation.
  A Team deadline could therefore cross after the last check and before the
  terminal transaction committed.
- SQLAlchemy invokes `before_commit` for nested savepoint commits as well as
  the root transaction. Tool audit persistence uses `begin_nested()`, so the
  deterministic Tool regression ignores nested commits and advances its clock
  only at the root terminal commit.
- PostgreSQL `CURRENT_TIMESTAMP` and `now()` are transaction-start values.
  They cannot detect a deadline crossed while the transaction is open; the
  final predicate must use advancing `clock_timestamp()`.

### RED evidence

Local root-commit boundary command, run before production changes on
`b0c94b0`:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/runtime/test_runner_gateway_state.py::test_team_success_crossing_deadline_immediately_before_commit_rolls_back "backend/tests/tools/test_gateway.py::test_team_tool_terminal_boundary_crossing_deadline_commits_no_success[before_commit]" --basetemp=backend/.tmp-final-review-fix3-round2-local-red-20260904-b
```

Result: `2 failed in 2.91s`. Runner completion returned HTTP 200 instead of
409, and successful Tool persistence did not raise `sandbox_timeout`; both
terminal successes committed after the test's root `before_commit` listener
advanced the application clock beyond the immutable Team deadline.

Live PostgreSQL database-clock command, also run before production changes:

```powershell
$env:PATH = "I:\智能体平台\IntelligentAgentPlatform\.worktrees\published-team-foundation\.testvenv-task5\Scripts;$env:PATH"
& .\backend\tests\support\run_postgres_tests.ps1 -PytestPath 'backend/tests/integration/test_runner_gateway_state_concurrency.py::test_team_completion_uses_database_time_at_commit_boundary'
```

Result: `1 failed in 9.11s`, zero skips. A root `before_commit` listener read
`clock_timestamp()`, slept until 250 ms beyond the five-second Team deadline,
and the frozen application clock remained before the deadline; completion
still did not raise. The disposable wrapper reported the expected failed test
and removed its PostgreSQL container.

### Implementation

- `backend/app/runtime/deadline_commit.py` provides the shared one-commit
  guard. It installs a per-session `before_commit` listener, performs a
  conditional status transition, requires exactly one updated row, and removes
  the listener in `finally` on both success and failure.
- PostgreSQL accepts the terminal transition only when
  `clock_timestamp() < :deadline`. SQLite and other local dialects use the
  existing injected/application clock as a bound boolean predicate, preserving
  deterministic historical fixtures.
- Successful Team completion leaves `AgentRun.status` at its prior committed
  value while the assistant message, completion/status events, and
  `RuntimeRunnerRequest` idempotency row flush. The guarded update performs the
  only transition to `completed`; a zero-row result rolls the transaction back
  and returns HTTP 409 `sandbox_timeout`.
- Successful Team Tool persistence likewise leaves `ToolInvocation.status` at
  `started` while result fields, the terminal event, and success audit flush.
  Its guarded update performs the only transition to `completed`; expiry rolls
  back those writes, preserves the previously committed started state, and
  raises `ToolRuntimeError("sandbox_timeout", ...)` without compensation.

### GREEN evidence

Exact local boundary rerun, including the retained during-flush Tool case:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/runtime/test_runner_gateway_state.py::test_team_success_crossing_deadline_immediately_before_commit_rolls_back "backend/tests/tools/test_gateway.py::test_team_tool_terminal_boundary_crossing_deadline_commits_no_success[before_commit]" "backend/tests/tools/test_gateway.py::test_team_tool_terminal_boundary_crossing_deadline_commits_no_success[after_flush]" --basetemp=backend/.tmp-final-review-fix3-round2-local-green-20260904-a
```

Result: `3 passed in 4.54s`.

Successful completion and terminal-persistence compatibility command:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/runtime/test_runner_gateway_state.py::test_completion_commits_final_message_status_and_artifact_references_once backend/tests/tools/test_gateway.py::test_records_tool_started_and_succeeded_with_context_and_parent backend/tests/tools/test_gateway.py::test_success_persists_invocation_and_ordered_safe_events --basetemp=backend/.tmp-final-review-fix3-round2-success-paths-green-20260904-a
```

Result: `3 passed in 3.60s`.

Direct guarded Team success and deadline-boundary command:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/runtime/test_runner_gateway_state.py::test_team_success_before_deadline_commits_guarded_status_and_result backend/tests/runtime/test_runner_gateway_state.py::test_team_success_crossing_deadline_immediately_before_commit_rolls_back backend/tests/tools/test_gateway.py::test_team_tool_terminal_success_before_deadline_commits_result "backend/tests/tools/test_gateway.py::test_team_tool_terminal_boundary_crossing_deadline_commits_no_success[before_commit]" "backend/tests/tools/test_gateway.py::test_team_tool_terminal_boundary_crossing_deadline_commits_no_success[after_flush]" --basetemp=backend/.tmp-final-review-fix3-round2-positive-boundary-green-20260904-a
```

Result: `5 passed in 5.97s`. The two positive cases exercise the guarded
`rowcount == 1` transitions and confirm that the same session observes the
committed Team Run and Tool statuses.

Initial directly affected local-module run before adding the two positive
guarded-transition cases:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider backend/tests/runtime/test_runner_gateway_state.py backend/tests/tools/test_gateway.py --basetemp=backend/.tmp-final-review-fix3-round2-core-modules-green-20260904-a
```

Result: `53 passed in 51.22s`.

Live PostgreSQL focused GREEN used the same command as the live RED with the
new implementation. Result: `1 passed in 8.41s`, zero skips, and successful
disposable-container cleanup.

Full live PostgreSQL concurrency/deadline module:

```powershell
$env:PATH = "I:\智能体平台\IntelligentAgentPlatform\.worktrees\published-team-foundation\.testvenv-task5\Scripts;$env:PATH"
& .\backend\tests\support\run_postgres_tests.ps1 -PytestPath 'backend/tests/integration/test_runner_gateway_state_concurrency.py'
```

Result: `8 passed in 10.01s`, zero skips. The wrapper exited successfully and
removed its disposable PostgreSQL container.

Final affected aggregate, including both positive guarded-transition cases:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider backend/tests/runtime/test_launcher_client.py backend/tests/runtime/test_workflow_runner.py backend/tests/runtime/test_gateway_model.py backend/tests/runtime/test_gateway_tools.py backend/tests/runtime/test_sandbox_runtime.py backend/tests/runtime/test_runner_gateway_state.py backend/tests/integration/test_runner_gateway_execution.py backend/tests/tools/test_gateway.py backend/tests/test_agents.py --basetemp=backend/.tmp-final-review-fix3-round2-affected-final-green-20260904-a
```

Result: `251 passed, 2 skipped in 185.78s`, zero failures. The only skips are
the expected Windows file-symlink and directory-symlink capability skips.

### Changed files

- `backend/app/runtime/deadline_commit.py` (new)
- `backend/app/runtime/runner_gateway_service.py`
- `backend/app/tools/gateway.py`
- `backend/tests/runtime/test_runner_gateway_state.py`
- `backend/tests/tools/test_gateway.py`
- `backend/tests/integration/test_runner_gateway_state_concurrency.py`
- `.superpowers/sdd/2026-09-01-published-team-review-remediation/final-review-fix3-report.md`

### Residual risks and self-review

- The shared PostgreSQL branch is exercised directly through Runner
  completion, while Tool Gateway exercises the same helper through SQLite's
  injected-clock branch. There is no separate live PostgreSQL Tool test.
- The conditional update defines the acceptance instant immediately before the
  physical commit. A schema-level deferred trigger would be a materially
  broader design and still executes before PostgreSQL finalizes the commit.
- Independent Round 2 review found no blocking correctness issue in listener
  ordering or cleanup, status transitions, ORM synchronization, rollback,
  nested audit transactions, compensation, or deterministic test behavior.
  The new helper and this report must be staged explicitly; unrelated existing
  untracked test artifacts remain excluded.

## Final committed scope

The authoritative range is `1365492..HEAD`, with base
`1365492a21969959cadaebd6b6ab766b3ad4d3cf`. The implementation content head
before this report-only amendment was
`ad0a3b7a2d17897ab7edbe7147cb69cd4231e246`; the amendment replaces that commit
object without changing any production or test blob. Across the initial
implementation and both fix rounds, the range contains 28 paths: 3 added and
25 modified, comprising 14 production files, 13 test files, and this report.

- `A` `.superpowers/sdd/2026-09-01-published-team-review-remediation/final-review-fix3-report.md`
- `M` `backend/app/agents/router.py`
- `M` `backend/app/conversations/dispatcher.py`
- `A` `backend/app/runtime/deadline_commit.py`
- `M` `backend/app/runtime/execution_snapshot.py`
- `M` `backend/app/runtime/gateway_model.py`
- `M` `backend/app/runtime/gateway_tools.py`
- `M` `backend/app/runtime/launcher_api.py`
- `M` `backend/app/runtime/launcher_client.py`
- `M` `backend/app/runtime/run_lifecycle.py`
- `M` `backend/app/runtime/runner_gateway_service.py`
- `M` `backend/app/runtime/sandbox_runtime.py`
- `M` `backend/app/runtime/workflow_runner.py`
- `M` `backend/app/runtime/workflow_runner_api.py`
- `M` `backend/app/tools/gateway.py`
- `M` `backend/tests/conversations/test_dispatcher.py`
- `M` `backend/tests/integration/test_runner_gateway_execution.py`
- `M` `backend/tests/integration/test_runner_gateway_state_concurrency.py`
- `A` `backend/tests/integration/test_runtime_deadline_e2e.py`
- `M` `backend/tests/runtime/test_launcher_api.py`
- `M` `backend/tests/runtime/test_launcher_client.py`
- `M` `backend/tests/runtime/test_run_lifecycle.py`
- `M` `backend/tests/runtime/test_runner_gateway_state.py`
- `M` `backend/tests/runtime/test_sandbox_runtime.py`
- `M` `backend/tests/runtime/test_workflow_runner.py`
- `M` `backend/tests/runtime/test_workflow_runner_api.py`
- `M` `backend/tests/test_agents.py`
- `M` `backend/tests/tools/test_gateway.py`

## Fix Round 3

### Investigation

- The dispatcher loaded the immutable snapshot before current-authorization
  reconstruction, but treated `None` as an Agent-compatible absence even when
  `AgentRun.actor_type == "team"`. A Team approval with a deleted snapshot
  therefore reached `ToolGateway.execute_approved()`.
- `ToolGateway.execute_approved()` committed `tool.started` and immediately
  called the builtin/MCP transport. A deadline that crossed during the started
  transaction was observed only by terminal persistence, after the external
  side effect had already occurred.
- Successful terminal Tool persistence also translated a missing snapshot to
  `deadline = None`, disabling both the application-clock check and the
  database-time conditional commit. This allowed a Team success result, event,
  and audit to commit without an immutable deadline.
- Agent runs intentionally predate mandatory immutable snapshots. Their
  no-snapshot approval path must remain executable.

### RED evidence

The first draft used an incomplete fake Tool result in two executor-admission
cases. It produced `tool_execution_failed` during output-schema validation and
was discarded as inconclusive for the intended error-code assertion. No
production file had been edited.

The corrected focused command, run unchanged against `5fe65c6`, was:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/conversations/test_dispatcher.py::test_team_approval_resume_without_snapshot_fails_before_gateway backend/tests/conversations/test_dispatcher.py::test_sandbox_dispatcher_executes_approved_agent_tool_without_snapshot_before_resuming_run backend/tests/tools/test_gateway.py::test_approved_team_tool_without_snapshot_fails_before_started_or_executor backend/tests/tools/test_gateway.py::test_approved_team_tool_crossing_deadline_during_started_commit_stops_before_executor backend/tests/tools/test_gateway.py::test_team_tool_missing_snapshot_at_terminal_persistence_commits_no_success backend/tests/tools/test_gateway.py::test_approved_agent_tool_without_snapshot_executes_after_digest_check --basetemp=backend/.tmp-final-review-fix3-round3-red-20260904-b
```

Result: `4 failed, 3 passed in 16.87s`.

- Dispatcher returned the Team Run id and called the mocked Gateway despite a
  missing snapshot.
- Approved Team Tool execution completed successfully with no snapshot.
- The root `before_commit` deadline race invoked the executor once before
  terminal persistence rejected the late success.
- Removing the snapshot during execution allowed terminal success to commit.
- Both parameterized Agent Gateway cases and the Agent dispatcher case passed,
  proving the compatibility baseline before implementation.

### Implementation

- The dispatcher now requires a digest-valid Team snapshot whose actor yields
  an immutable Team deadline. Missing, invalid, non-Team, and expired Team
  snapshots fail with `sandbox_timeout` before authorization can reach the
  Gateway.
- `ToolGateway` owns the definitive admission rule. It locks the active Run,
  requires a valid Team snapshot/deadline, and checks the injected wall clock
  both before `tool.started` and again immediately after that transaction
  commits, before the builtin/MCP transport is invoked.
- The post-start admission transaction is read-only and explicitly rolled back
  before transport. Invocation arguments and durable audit identifiers are
  copied first, preserving the existing guarantee that external executors run
  without an open application database transaction.
- Successful terminal persistence reuses the same Team snapshot/deadline
  requirement. A missing snapshot now follows the existing terminal fence:
  terminal writes roll back, compensation is bypassed, and only the previously
  committed `tool.started` state remains.
- Agent approval execution still permits no snapshot. Existing replay,
  idempotency, failure compensation, and terminal-run fences remain unchanged.
- Direct PostgreSQL coverage now holds the successful Tool terminal commit
  beyond a five-second Team deadline while freezing the application clock. The
  `clock_timestamp()` predicate rejects the transition after the executor has
  run once, leaving no result, completion event, or success audit.

### GREEN evidence

Corrected focused rerun:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/conversations/test_dispatcher.py::test_team_approval_resume_without_snapshot_fails_before_gateway backend/tests/conversations/test_dispatcher.py::test_sandbox_dispatcher_executes_approved_agent_tool_without_snapshot_before_resuming_run backend/tests/tools/test_gateway.py::test_approved_team_tool_without_snapshot_fails_before_started_or_executor backend/tests/tools/test_gateway.py::test_approved_team_tool_crossing_deadline_during_started_commit_stops_before_executor backend/tests/tools/test_gateway.py::test_team_tool_missing_snapshot_at_terminal_persistence_commits_no_success backend/tests/tools/test_gateway.py::test_approved_agent_tool_without_snapshot_executes_after_digest_check --basetemp=backend/.tmp-final-review-fix3-round3-green-20260904-a
```

Result: `7 passed in 13.68s`.

A final pre-commit rerun of the same seven focused cases, using basetemp
`backend/.tmp-final-review-fix3-round3-final-focused-20260904-a`, passed in
`16.39s`.

The existing current-membership Team test originally omitted a snapshot and
therefore reached the new earlier fence. Its fixture was corrected with a valid
unexpired immutable Team snapshot so it continues to isolate authorization.
Focused result: `1 passed in 3.50s`.

Complete dispatcher and Tool Gateway modules:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider backend/tests/conversations/test_dispatcher.py backend/tests/tools/test_gateway.py --basetemp=backend/.tmp-final-review-fix3-round3-modules-green-20260904-b
```

Result: `52 passed in 79.04s`.

Runner replay/idempotency, deadline state/listener, approved-Agent resume, and
HTTP error-contract modules:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider backend/tests/runtime/test_runner_gateway_state.py backend/tests/runtime/test_runner_gateway_api.py backend/tests/integration/test_runner_gateway_failures.py backend/tests/integration/test_runner_gateway_execution.py --basetemp=backend/.tmp-final-review-fix3-round3-contracts-green-20260904-a
```

Result: `48 passed in 10.09s`.

Focused direct PostgreSQL Team Tool boundary:

```powershell
$env:PATH = "I:\智能体平台\IntelligentAgentPlatform\.worktrees\published-team-foundation\.testvenv-task5\Scripts;$env:PATH"
& .\backend\tests\support\run_postgres_tests.ps1 -PytestPath 'backend/tests/integration/test_runner_gateway_state_concurrency.py::test_team_tool_success_uses_database_time_at_commit_boundary'
```

Result: `1 passed in 8.90s`, zero skips. The first sandboxed invocation could
not access the local Docker named pipe; the approved escalated invocation
succeeded and the wrapper removed its disposable PostgreSQL container.

Full live PostgreSQL concurrency/deadline module:

```powershell
$env:PATH = "I:\智能体平台\IntelligentAgentPlatform\.worktrees\published-team-foundation\.testvenv-task5\Scripts;$env:PATH"
& .\backend\tests\support\run_postgres_tests.ps1 -PytestPath 'backend/tests/integration/test_runner_gateway_state_concurrency.py'
```

Result: `9 passed in 16.02s`, zero skips, with successful disposable-container
cleanup.

Expanded affected aggregate:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider backend/tests/conversations/test_dispatcher.py backend/tests/runtime/test_launcher_client.py backend/tests/runtime/test_workflow_runner.py backend/tests/runtime/test_gateway_model.py backend/tests/runtime/test_gateway_tools.py backend/tests/runtime/test_sandbox_runtime.py backend/tests/runtime/test_runner_gateway_state.py backend/tests/runtime/test_runner_gateway_api.py backend/tests/integration/test_runner_gateway_execution.py backend/tests/integration/test_runner_gateway_failures.py backend/tests/tools/test_gateway.py backend/tests/test_agents.py --basetemp=backend/.tmp-final-review-fix3-round3-affected-green-20260904-a
```

Result: `285 passed, 2 skipped in 268.05s`. The only skips are the existing
Windows file-symlink and directory-symlink capability skips.

### Changed files

- `backend/app/conversations/dispatcher.py`
- `backend/app/tools/gateway.py`
- `backend/tests/conversations/test_dispatcher.py`
- `backend/tests/tools/test_gateway.py`
- `backend/tests/integration/test_runner_gateway_state_concurrency.py`
- `.superpowers/sdd/2026-09-01-published-team-review-remediation/final-review-fix3-report.md`

All five production/test paths already belong to the authoritative 28-path
`1365492..HEAD` review scope, so Fix Round 3 introduces no new path to that
enumeration.

### Residual risks and self-review

- The definitive transport admission check occurs after the durable started
  transaction and immediately before the external call. As with any cooperative
  wall-clock fence, it cannot recall a transport once that call has begun; late
  terminal success is independently rejected by the database-time commit guard.
- A timed-out approved Tool intentionally remains `started`. The dispatcher
  persists the Run-level `failed/sandbox_timeout` terminal state, while Tool
  success result/event/audit persistence is forbidden.
- The focused race is deterministic: it advances the injected clock only at
  the root started commit, ignores nested audit savepoints, and asserts that the
  real executor wrapper was never entered.
- The PostgreSQL test uses the real migrated tables, Run and snapshot rows,
  Approval/ToolInvocation locks, ToolStore, ToolGateway, audit recorder, and
  database clock. Only the external builtin is wrapped to count admission.

## Fix Round 4

### Investigation

- `ApprovalService.prepare_execution()` and both approved-Run admission checks
  use the Gateway repository session. The durable `tool.started` invocation,
  event, and audit commit first; the second admission then locks the Run,
  integrity-checks the immutable Team snapshot, derives its absolute deadline,
  and rolls back the read transaction before external work.
- `McpStore.get()` performs registry/config lookup in its own short-lived
  session, while `McpCredentialResolver.resolve()` validates scope and
  deep-copies headers in memory. `McpProtocolClient.call_tool()` is the external
  transport boundary.
- The round-3 post-start admission happened before both MCP preparation steps.
  Its transaction was correctly closed, but its deadline result was discarded.
  Consequently, the immutable Team deadline could expire during registry or
  credential preparation and `call_tool()` would still begin. The later
  terminal fence prevented successful persistence but could not prevent the
  external side effect.
- Root-cause hypothesis before editing: the MCP branch had no way to carry the
  authoritative post-start Team deadline to its actual transport boundary, so
  it used stale admission after preparation. The focused RED confirmed this by
  recording one schema-valid MCP call after credential resolution crossed the
  injected deadline.

### RED evidence

The deterministic Team race and Agent compatibility characterization were
added before production edits and run against unchanged `cc3816e`:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/tools/test_gateway.py::test_approved_team_mcp_tool_crossing_deadline_during_setup_stops_before_transport backend/tests/tools/test_gateway.py::test_approved_agent_mcp_tool_remains_snapshot_optional_during_setup --basetemp=backend/.tmp-final-review-fix3-round4-red-20260904-a
```

Result: `1 failed, 1 passed in 6.32s`. The Team failure was exactly
`transport_calls == []`: the contract-valid protocol fake had been entered once
with the resolved scoped credential and expected MCP arguments. The Agent
snapshot-optional compatibility case passed on the unchanged baseline.

The Team test delegates to the real persisted `McpStore` and the real
`McpCredentialResolver`; thin wrappers only record preparation and advance the
injected clock after credential resolution. A fresh verification session proves
the exact durable state: Run `queued`, Approval `approved`, invocation `started`
with no result, error, completion time, or duration; events contain only
`approval.requested`, `run.status`, and `tool.started`; Tool audits contain only
`tool.invoke.started`.

### Implementation

- `_lock_admitted_run()` and the MCP boundary now share
  `_require_before_deadline()`, avoiding a second or divergent interpretation
  of the immutable Team deadline.
- `_admit_approved_transport()` preserves the existing post-start locked
  admission and rollback, and returns its immutable deadline. Builtin execution
  remains behind that same check.
- Only a non-`None` Team deadline creates an MCP boundary callback.
  `_execute_mcp()` invokes it after registry/config and credential preparation
  and directly before `call_tool()`. The callback is a pure injected-clock
  comparison and therefore opens no application transaction across transport.
- A private transport-admission exception bypasses ordinary Tool failure
  persistence. Deadline refusal retains the established durable `started`
  state for dispatcher-level `failed/sandbox_timeout` handling instead of
  creating a misleading `tool.failed` terminal record.
- Agent deadlines remain `None`, so approved Agent MCP execution receives no
  added boundary callback. Ordinary non-approved MCP execution and all builtin
  execution APIs remain unchanged.

### GREEN evidence

Focused final verification after strengthening durable-state assertions to use
a fresh database session:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/tools/test_gateway.py::test_approved_team_mcp_tool_crossing_deadline_during_setup_stops_before_transport backend/tests/tools/test_gateway.py::test_approved_agent_mcp_tool_remains_snapshot_optional_during_setup --basetemp=backend/.tmp-final-review-fix3-round4-focused-final-green-20260904-a
```

Result: `2 passed in 4.65s`.

Complete Tool Gateway module:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider backend/tests/tools/test_gateway.py --basetemp=backend/.tmp-final-review-fix3-round4-tool-module-final-green-20260904-a
```

Result: `41 passed in 81.91s`.

Dispatcher approval resume plus Runner/Gateway HTTP mapping, replay,
idempotency, listener/state fence, and compensation-facing contracts:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider backend/tests/conversations/test_dispatcher.py backend/tests/runtime/test_runner_gateway_state.py backend/tests/runtime/test_runner_gateway_api.py backend/tests/runtime/test_gateway_tools.py backend/tests/integration/test_runner_gateway_failures.py backend/tests/integration/test_runner_gateway_execution.py --basetemp=backend/.tmp-final-review-fix3-round4-contracts-green-20260904-a
```

Result: `71 passed in 27.70s`.

Affected Tool, MCP, dispatcher, transport, sandbox, and Runner/Gateway
aggregate:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider backend/tests/conversations/test_dispatcher.py backend/tests/runtime/test_launcher_client.py backend/tests/runtime/test_workflow_runner.py backend/tests/runtime/test_gateway_model.py backend/tests/runtime/test_gateway_tools.py backend/tests/runtime/test_sandbox_runtime.py backend/tests/runtime/test_runner_gateway_state.py backend/tests/runtime/test_runner_gateway_api.py backend/tests/integration/test_runner_gateway_execution.py backend/tests/integration/test_runner_gateway_failures.py backend/tests/tools/test_gateway.py backend/tests/mcp/test_protocol_client.py backend/tests/mcp/test_credentials.py backend/tests/test_mcp.py --basetemp=backend/.tmp-final-review-fix3-round4-affected-green-20260904-a
```

Result: `255 passed in 267.16s`, with zero skips.

Direct PostgreSQL was not rerun for this round. The change adds a pure
application-clock admission immediately before MCP transport and does not alter
the terminal database-time conditional commit already covered by the retained
live PostgreSQL tests from Fix Round 3.

### Changed files and scope consistency

- `backend/app/tools/gateway.py`
- `backend/tests/tools/test_gateway.py`
- `.superpowers/sdd/2026-09-01-published-team-review-remediation/final-review-fix3-report.md`

All three paths already belong to the authoritative final-review-fix3 committed
scope. No new source, test, or report path was introduced, and all unrelated
and untracked artifacts were preserved.

### Residual risks and self-review

- Admission is cooperative: it prevents `call_tool()` from starting when the
  deadline is already expired, but cannot recall transport after the call has
  begun. The independent terminal application-clock and database-time guards
  still prevent a late successful Tool result, event, or audit from committing.
- `McpProtocolClient.call_tool()` may issue several protocol requests after its
  boundary is entered. Per-request cancellation is outside this finding; the
  required external-call admission now occurs directly before that boundary.
- The mutation check is direct: deleting the boundary callback, passing it for
  Agents, moving it above credential resolution, or treating its refusal as an
  ordinary Tool failure breaks one of the new focused assertions.
- Builtin admission, Team missing-snapshot fail-closed behavior, Agent optional
  snapshots, no-open-transaction transport, replay/idempotency, HTTP
  `sandbox_timeout` identity, listener cleanup, compensation behavior, and the
  terminal database-time guard were not weakened. Focused, module, contract,
  and affected aggregate verification all passed on the final production diff.
