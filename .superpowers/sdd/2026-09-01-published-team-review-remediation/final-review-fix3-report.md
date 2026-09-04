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

This is the complete 23-file commit scope: 11 production files, 11 test files,
and this report. Virtual environments, downloaded dependencies, basetemps,
logs, XML, build output, and all other pre-existing untracked artifacts were
excluded.

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
