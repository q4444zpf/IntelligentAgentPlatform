# Final Review Fix 2: Production-Hard Team Timeout Report

## Status

DONE. The Team execution deadline is now owned by the coordinator, propagated
separately from the wider request/token deadline, enforced at the worker and
transport boundaries, and backed by terminal Run-row fences for platform
persistence. No Agent-default files were changed by this work. The shared
fail-fast changes already present in `run_lifecycle.py` and
`runner_gateway_service.py` were preserved.

## Root Causes

The initial focused test-first gate reported `22 failed, 0 passed`; that RED
result and its static review are recorded in
`final-review-fix2-team-timeout-red-test-review.md`. The failures exposed four
connected gaps:

1. Team timeout discovery happened only after the worker fetched its snapshot,
   while the coordinator watched the wider request deadline.
2. abandoned `ThreadPoolExecutor` work used non-daemon workers and therefore
   delayed interpreter/container exit even after `SandboxRuntime.execute()`
   returned.
3. all Runner Gateway traffic shared the wider deadline, including operations
   started by abandoned Team work; completion had no distinct bounded control
   budget or authoritative timeout exit fallback.
4. local timeout Events did not serialize with already-entered database
   callbacks, and model, Tool, and artifact paths could finish platform
   persistence after a terminal response.

## Production Changes

### Deadline ownership and process exit

- `backend/app/runtime/execution_contract.py` adds a timezone-aware
  `execution_deadline_at` that cannot exceed the wider request deadline.
- `backend/app/runtime/execution_snapshot.py`, `launcher_api.py`,
  `launcher_client.py`, `workflow_runner.py`, `workflow_runner_api.py`, and
  `container_launcher.py` propagate the immutable execution deadline into the
  launched worker.
- `backend/app/runtime/run_lifecycle.py` derives the Team deadline from the
  coordinator-created verified snapshot and arms both execute and recovery
  watchdogs from the same effective monotonic remainder. Timeout worker exit
  code `4` maps authoritatively to `sandbox_timeout`, including when the
  worker's completion request does not commit.
- `backend/app/runtime/sandbox_runtime.py` converts the UTC execution deadline
  once to a monotonic deadline and replaces executor abandonment with small
  daemon `Thread` operations. Planning, member, and synthesis waits do not join
  abandoned work. The shared `_wait_before_team_deadline()` sets the local
  checkpoint fence both when the deadline calculation itself raises and when
  `Event.wait()` times out.
- `backend/app/runtime/run_worker.py` reserves exit code `4` for
  `sandbox_timeout`.

### Transport deadline separation

- `backend/app/runtime/runner_gateway_client.py` stores immutable request and
  execution deadlines. Every execution request clamps connect, pool, read, and
  write timeouts to the positive execution remainder and fails before opening
  transport when it is exhausted.
- Only `complete()` selects a separate per-call control deadline:
  `min(request_deadline, now + 1 second)`. It never mutates or widens the
  execution deadline observed by concurrent abandoned work.

### Durable terminal fences

- `backend/app/runtime/runner_gateway_service.py` locks the Run row with
  `populate_existing=True` and `FOR UPDATE`. Checkpoint, event, model, outer
  Tool, artifact-capability, and artifact mutations reject non-running Runs.
  Idempotent endpoints check exact replay before checking active status.
- Model provider work, Tool execution, and artifact storage upload happen
  outside Run-lock transactions. Their terminal platform writes reacquire and
  refresh the Run lock before persisting results, events, audits, requests, or
  artifact rows.
- `backend/app/artifacts/service.py` splits artifact validation/preparation,
  object upload, and database persistence. The Runner Gateway deletes a newly
  uploaded object if a terminal completion or exact replay wins the final
  database race, or if final persistence fails.
- `backend/app/tools/gateway.py` applies the same refreshed Run-row fence to
  Tool start and terminal writes. Normal Tool calls remain `running`-only;
  approved calls consistently allow only `queued` and `waiting_approval` at
  start, terminal persistence, and compensation. A terminal race propagates
  `run_not_active` instead of being rewritten as `audit_persistence_failed`.

Artifact-capability registration has no idempotency-key/replay contract, so it
uses the active Run fence but does not claim replay semantics that its API does
not expose.

## Tests Changed

- `backend/tests/integration/test_runner_gateway_state_concurrency.py` (new)
- `backend/tests/runtime/test_artifact_backend.py`
- `backend/tests/runtime/test_container_launcher.py`
- `backend/tests/runtime/test_execution_contract.py`
- `backend/tests/runtime/test_gateway_model.py`
- `backend/tests/runtime/test_gateway_tools.py`
- `backend/tests/runtime/test_launcher_api.py`
- `backend/tests/runtime/test_launcher_client.py`
- `backend/tests/runtime/test_run_lifecycle.py`
- `backend/tests/runtime/test_runner_gateway_client.py`
- `backend/tests/runtime/test_runner_gateway_state.py`
- `backend/tests/runtime/test_sandbox_runtime.py`
- `backend/tests/runtime/test_workflow_runner.py`
- `backend/tests/runtime/test_workflow_runner_api.py`
- `backend/tests/tools/test_gateway.py`

The PostgreSQL module uses independent sessions and real `FOR UPDATE` locking.
It checks `pg_blocking_pids()` and covers checkpoint/event serialization in
both commit orders plus artifact-capability serialization in both orders.

## RED and Mutation Evidence

Initial focused node-list gate across the new contract, coordinator, worker,
transport, checkpoint/event, model, Tool, capability, and artifact tests:

```text
22 failed, 0 passed
```

The result is independently captured in the RED-test review named above.

The first PostgreSQL checkpoint/event gate passed all four cases. Removing the
Runner Gateway `FOR UPDATE` lock as a controlled mutation then produced:

```text
4 failed in 24.03s
```

All four failures showed that no row-lock blocking occurred. Restoring the lock
and adding artifact-capability orderings produced the final six-case GREEN.

The Tool compensation regression first failed because the terminal race was
reported as `audit_persistence_failed`; after propagating `run_not_active`, the
node passed. The approved Tool status/race tests first produced `3 failed`;
threading the explicit approved statuses through start, persistence, and
compensation produced `4 passed` when the compensation node was included.

The final local early-fence regression was run before its production fix:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend\tests\runtime\test_sandbox_runtime.py::test_team_deadline_expiry_before_wait_sets_late_checkpoint_fence[member-2] backend\tests\runtime\test_sandbox_runtime.py::test_team_deadline_expiry_before_wait_sets_late_checkpoint_fence[synthesis-3] -q --basetemp backend\.tmp-final-review-fix2-team-timeout-late-fence-red-20260904-c
```

Output: `2 failed, 2 warnings in 4.19s`. Both executions returned
`sandbox_timeout`, but releasing abandoned work added one late checkpoint
(`3 != 2` for member and `5 != 4` for synthesis). This proved that expiry
raised while calculating the wait duration without setting the callback fence.

## GREEN Verification

The corrected early-fence nodes:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend\tests\runtime\test_sandbox_runtime.py::test_team_deadline_expiry_before_wait_sets_late_checkpoint_fence[member-2] backend\tests\runtime\test_sandbox_runtime.py::test_team_deadline_expiry_before_wait_sets_late_checkpoint_fence[synthesis-3] -q --basetemp backend\.tmp-final-review-fix2-team-timeout-late-fence-green-20260904-a
```

Output: `2 passed, 1 warning in 2.42s`.

Slow planning/member/synthesis, real child-worker process exit, and failed
completion-report fallback:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend\tests\runtime\test_sandbox_runtime.py::test_team_timeout_interrupts_slow_planning_before_wider_run_deadline backend\tests\runtime\test_sandbox_runtime.py::test_team_timeout_interrupts_slow_member_before_wider_run_deadline backend\tests\runtime\test_sandbox_runtime.py::test_team_timeout_interrupts_slow_synthesis_before_wider_run_deadline backend\tests\runtime\test_sandbox_runtime.py::test_real_worker_process_does_not_join_abandoned_team_deadline_work backend\tests\runtime\test_sandbox_runtime.py::test_worker_timeout_exit_survives_failed_completion_report -q --basetemp backend\.tmp-final-review-fix2-team-timeout-local-gate-green-20260904-a
```

Output: `7 passed, 1 warning in 21.06s`.

Approved Tool `waiting_approval`/production `queued`, terminal race, and
compensation:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend\tests\tools\test_gateway.py::test_approved_tool_invocation_executes_after_digest_check backend\tests\tools\test_gateway.py::test_approved_tool_finishing_after_terminal_run_commits_no_terminal_state backend\tests\tools\test_gateway.py::test_terminal_run_during_completion_compensation_bypasses_retry -q --basetemp backend\.tmp-final-review-fix2-team-timeout-approved-tool-final-green-20260904-a
```

Output: `4 passed, 1 warning in 7.81s`.

Complete affected aggregate, including conversation dispatcher coverage:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend\tests\runtime\test_execution_contract.py backend\tests\runtime\test_runner_gateway_client.py backend\tests\runtime\test_runner_gateway_state.py backend\tests\runtime\test_gateway_model.py backend\tests\runtime\test_gateway_tools.py backend\tests\runtime\test_run_lifecycle.py backend\tests\runtime\test_sandbox_runtime.py backend\tests\runtime\test_artifact_backend.py backend\tests\runtime\test_workflow_runner.py backend\tests\runtime\test_workflow_runner_api.py backend\tests\runtime\test_launcher_client.py backend\tests\runtime\test_launcher_api.py backend\tests\runtime\test_container_launcher.py backend\tests\artifacts\test_service.py backend\tests\tools\test_gateway.py backend\tests\conversations\test_dispatcher.py -q --basetemp backend\.tmp-final-review-fix2-team-timeout-affected-final-green-20260904-a
```

Output: `265 passed, 1 warning in 157.37s`.

Real PostgreSQL concurrency gate, with the repository wrapper creating and
removing a disposable PostgreSQL 16 container:

```powershell
.\backend\tests\support\run_postgres_tests.ps1 -PytestPath backend\tests\integration\test_runner_gateway_state_concurrency.py
```

Output: `6 passed in 4.38s`; no cases were skipped.

An earlier broad affected run produced `249 passed, 1 failed in 145.22s`.
The sole failure was the approved `waiting_approval` status regression; the
explicit approved-status fix above addresses it, and the fresh 265-test
aggregate is the post-fix evidence.

Pytest's only warning in local venv runs is the expected inability to write the
shared worktree's `.pytest_cache` directory.

## Self-Review

- Checkpoint/event ordering is exact replay, active-Run check, mutation,
  request row, and commit while holding the same Run lock used by completion.
- `populate_existing=True` prevents an `expire_on_commit=False` identity map
  from hiding a terminal state committed by another session.
- Model and Tool provider calls and artifact object upload occur only after the
  pre-call transaction is committed; no Run lock crosses external work.
- Post-external success and failure paths reacquire the Run lock before any
  result, event, audit, request, capability, or artifact database commit.
- Artifact replay, terminal-race, duplicate, validation, and persistence-error
  paths clean a newly uploaded object where one exists.
- Approved Tool execution preserves the Run status for coordinator restart and
  uses the same allowed-status set through compensation.
- Exact terminal replay remains available for APIs with an idempotency
  contract; new terminal mutations are rejected.
- `git diff --check` reports no whitespace errors. Its output contains only
  expected LF-to-CRLF working-copy notices on Windows.

## Residual Constraint

External model, Tool, or storage work accepted before the deadline may not be
cancellable. Daemon abandonment, transport deadlines, and coordinator
termination bound the platform process and caller, while Run-row fencing
ensures that no result, event, audit, request, capability, or artifact database
state from that late work commits after the terminal response. An object
uploaded before losing the final race is deleted best-effort; a storage cleanup
failure is logged for operational remediation.

No files were staged, committed, reset, or cleaned.

## Formal Review Correction Round 1

### Status

DONE. The formal review's three P1 deadline findings and P2 clock-domain
finding are corrected. Coordinator calls and sleeps now honor one immutable
monotonic watchdog, production HTTP transports impose end-to-end bounds, an
execution-budget expiry remains authoritative as `sandbox_timeout`, and the
Team runtime boundary now declares a monotonic `float` deadline explicitly.

### Root Causes and Corrections

1. Coordinator polling had a deadline value but synchronous submit/status
   calls and an unclamped sleep could prevent the watchdog from examining it.
   Execute and recovery now pass the immutable monotonic deadline through
   Workflow Runner calls, recheck after each response, clamp sleeps, and map a
   runner error observed after expiry back to `sandbox_timeout`. Timeout
   terminate and cleanup share the single `watchdog_deadline + 1 second`
   control allowance; ordinary cancellation, retry, and cleanup remain on
   their existing lifecycle behavior.
2. Workflow Runner, Sandbox Launcher, and Runner Gateway used HTTPX phase
   timeouts as though they were total request deadlines. Production
   Workflow Runner and Launcher transports now use `httpx.AsyncClient` under
   an outer `asyncio.timeout`, recompute the monotonic remainder inside the
   async operation, and recheck after response decoding. Runner Gateway
   streams under the same absolute monotonic bound and rechecks after body
   collection, JSON decoding, and schema validation.
3. Runner Gateway transport expiry previously collapsed into
   `runner_gateway_unavailable`. Execution calls now raise the distinct
   `RunnerGatewayDeadlineExceeded` with code `sandbox_timeout`; the bounded
   completion control call intentionally remains
   `runner_gateway_unavailable`. SandboxRuntime also rechecks its immutable
   monotonic execution deadline when a gateway/model wrapper reports late.
4. Workflow Runner's first downstream validation selected
   `header OR execution body`, allowing a later hop header to mask an expired
   immutable execution deadline. It now validates both independently and
   propagates their earlier normalized UTC value. The Launcher repeats the
   pre-hop validation, while the controlled launcher removes a container
   created or readied after the immutable execution deadline before it can be
   registered.
5. `_execute_team` now accepts `monotonic_deadline: float`. Planning, member,
   and synthesis work uses daemon deadline operations and monotonic waits, so
   abandoned work cannot keep the worker process alive. Late checkpoint
   callbacks are fenced before and after the state lock.

### Test-First and Mutation Evidence

The first correction RED covered blocked coordinator submit/status/recovery,
late runner errors, sleep clamping, the shared teardown allowance, Workflow
Runner slow-drip traffic, and launcher pre/post-create expiry:

```text
9 failed
```

The two-hop deadline gate then produced `3 failed`; the lifecycle and launcher
compensation gate produced `4 failed`; Runner Gateway execution/completion
slow-drip requests produced `2 failed`; runtime late-error/type composition
produced `2 failed`; and the model-wrapper mapping produced `1 failed`. The
corresponding focused GREEN results were `12 passed`, `5 passed`, `2 passed`,
`4 passed`, and `1 passed`. Removing the coordinator's post-status deadline
recheck as a controlled mutation made the late successful-exit case persist
`completed`; restoring the check returned it to `sandbox_timeout`.

Final self-review added two more boundary regressions. The exact RED command
was:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend/tests/runtime/test_workflow_runner_api.py::test_runner_rejects_expired_execution_with_later_hop_deadline backend/tests/runtime/test_runner_gateway_client.py::test_execution_deadline_is_rechecked_after_response_validation -q --basetemp .tmp\pytest-team-timeout-self-review-red-20260904
```

Output: `2 failed in 1.37s`. The route returned `200` when an expired body
deadline was paired with a later hop deadline, and schema validation could
finish after the execution budget without raising. After the independent
deadline/minimum and post-validation fixes, the same nodes under the unique
GREEN basetemp produced `2 passed in 1.09s`; the final post-report rerun
produced `2 passed in 1.14s`.

### Fresh Verification

Model-wrapper timeout authority:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend/tests/runtime/test_sandbox_runtime.py::test_model_gateway_error_observed_after_execution_deadline_reports_timeout -q --basetemp .tmp\pytest-team-timeout-model-wrapper-green-20260904
```

Output: `1 passed in 3.85s`.

Real child-worker exit for planning/member/synthesis and failed completion
fallback:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend/tests/runtime/test_sandbox_runtime.py::test_real_worker_process_does_not_join_abandoned_team_deadline_work backend/tests/runtime/test_sandbox_runtime.py::test_worker_timeout_exit_survives_failed_completion_report -q --basetemp backend\.tmp-final-review-fix2-team-timeout-round1-worker-exit-green-20260904-a
```

Output: `4 passed in 15.62s`. An earlier parallel invocation used two sibling
basetemps below a missing shared `.tmp` parent and reported three fixture setup
errors before those cases ran; the sequential unique-root result above is the
canonical worker evidence.

Complete affected aggregate, including coordinator recovery/cleanup and the
conversation dispatcher:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend\tests\runtime\test_execution_contract.py backend\tests\runtime\test_runner_gateway_client.py backend\tests\runtime\test_runner_gateway_state.py backend\tests\runtime\test_gateway_model.py backend\tests\runtime\test_gateway_tools.py backend\tests\runtime\test_run_lifecycle.py backend\tests\runtime\test_sandbox_runtime.py backend\tests\runtime\test_artifact_backend.py backend\tests\runtime\test_workflow_runner.py backend\tests\runtime\test_workflow_runner_api.py backend\tests\runtime\test_launcher_client.py backend\tests\runtime\test_launcher_api.py backend\tests\runtime\test_container_launcher.py backend\tests\artifacts\test_service.py backend\tests\tools\test_gateway.py backend\tests\conversations\test_dispatcher.py -q --basetemp backend\.tmp-final-review-fix2-team-timeout-round1-affected-green-20260904-a
```

Output: `290 passed in 178.20s`.

Real PostgreSQL serialization gate:

```powershell
.\backend\tests\support\run_postgres_tests.ps1 -PytestPath backend\tests\integration\test_runner_gateway_state_concurrency.py
```

The sandboxed attempt was blocked before collection by denied Docker named-pipe
access. The approved rerun of the same command produced `6 passed in 4.94s`,
with no skips. Local pytest runs otherwise emitted only the known shared
`.pytest_cache` permission warning.

`git diff --check` exits zero. Its output contains only the expected Windows
LF-to-CRLF working-copy notices and no whitespace errors.

### Round 1 Files Changed

Production:

- `backend/app/runtime/run_lifecycle.py`
- `backend/app/runtime/workflow_runner.py`
- `backend/app/runtime/launcher_client.py`
- `backend/app/runtime/workflow_runner_api.py`
- `backend/app/runtime/launcher_api.py`
- `backend/app/runtime/container_launcher.py`
- `backend/app/runtime/runner_gateway_client.py`
- `backend/app/runtime/sandbox_runtime.py`

Tests:

- `backend/tests/runtime/test_run_lifecycle.py`
- `backend/tests/runtime/test_workflow_runner.py`
- `backend/tests/runtime/test_launcher_client.py`
- `backend/tests/runtime/test_workflow_runner_api.py`
- `backend/tests/runtime/test_launcher_api.py`
- `backend/tests/runtime/test_container_launcher.py`
- `backend/tests/runtime/test_runner_gateway_client.py`
- `backend/tests/runtime/test_sandbox_runtime.py`

### Round 1 Self-Review

- UTC deadlines are converted to a monotonic remainder once at each process
  boundary; production async operations recompute their actual remainder and
  post-response paths reject late success.
- Workflow Runner validates the immutable body execution deadline separately
  from the optional hop deadline and forwards the earlier value.
- Launcher creation checks expiry before create, after create, and after
  readiness. Late containers are force-removed and never entered in the
  run registry.
- Runner Gateway checks the total budget after I/O, JSON parsing, and schema
  validation. Execution expiry is `sandbox_timeout`; completion expiry remains
  `runner_gateway_unavailable` and cannot widen concurrent execution traffic.
- Coordinator success/failure responses are rechecked against the watchdog
  before persistence. Exit code `4` remains authoritative, including when the
  worker completion report fails.
- Only timeout-owned terminal teardown receives the shared one-second control
  deadline. Cancellation, explicit lifecycle operations, and cleanup retry
  were not silently changed into bounded timeout control calls.
- No Agent-default or approval-router files were changed by Round 1. No files
  were staged, committed, reset, cleaned, or deleted.

## Acceptance E2E Fixture Correction

The controller acceptance command exposed one stale non-timeout E2E fixture:

```powershell
$env:PYTHONPATH='backend'; .\.testvenv-task5\Scripts\python.exe -m pytest tests/e2e/test_published_team_run.py backend/tests/runtime/test_runner_gateway_nginx_boundary.py backend/tests/collaboration backend/tests/identity/test_authorization.py -q --basetemp backend\.tmp-final-review-fix2-team-e2e-security-20260904-a
```

Controller RED: `1 failed, 48 passed`. The failing fixture omitted the now
required `execution_deadline_at`. A focused reproduction produced `1 failed`
in `7.89s`. The fixture now assigns one explicit future value to both
`deadline_at` and `execution_deadline_at`; its behavior and assertions are
unchanged.

```powershell
$env:PYTHONPATH='backend'; .\.testvenv-task5\Scripts\python.exe -m pytest tests/e2e/test_published_team_run.py::test_published_team_runtime_completes_two_member_tasks_and_one_synthesis -q --basetemp backend\.tmp-final-review-fix2-team-e2e-fixture-green-20260904-b
```

Focused GREEN: `1 passed in 5.35s`.

```powershell
$env:PYTHONPATH='backend'; .\.testvenv-task5\Scripts\python.exe -m pytest tests/e2e/test_published_team_run.py backend/tests/runtime/test_runner_gateway_nginx_boundary.py backend/tests/collaboration backend/tests/identity/test_authorization.py -q --basetemp backend\.tmp-final-review-fix2-team-e2e-security-green-20260904-b
```

Acceptance GREEN: `49 passed in 9.76s`. Both runs emitted only the known
shared `.pytest_cache` permission warning. This correction changed only
`tests/e2e/test_published_team_run.py`; no production code was changed.

## Full Backend Slow-Drip Classification Correction

The full backend run exposed a Windows timer-resolution race in the Runner
Gateway client's error classification. `asyncio.timeout` is the client's own
absolute request guard, but Windows reports a `0.015625s` monotonic clock
resolution and may dispatch that timeout callback one clock tick early. The
resulting built-in `TimeoutError` was incorrectly grouped with ordinary HTTPX
errors and then downgraded to `runner_gateway_unavailable` when a following
monotonic read still appeared just before the deadline.

A deterministic regression pins a one-second monotonic budget, makes
`_async_request_content` raise the guard's built-in `TimeoutError`, and makes
the next tick read `100.999` against deadline `101.0`:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend/tests/runtime/test_runner_gateway_client.py::test_execution_absolute_guard_timeout_is_authoritative_before_next_clock_tick -q --basetemp backend\.tmp-final-review-fix2-team-timeout-absolute-guard-red-20260904-b
```

RED: `1 failed in 2.47s`; the expected
`RunnerGatewayDeadlineExceeded` was instead `RunnerGatewayUnavailable`.

The minimal correction handles the client's built-in absolute-guard
`TimeoutError` separately. Execution calls map it authoritatively to
`RunnerGatewayDeadlineExceeded`/`sandbox_timeout`; explicit completion/control
calls retain `RunnerGatewayUnavailable`. `httpx.HTTPError` keeps its existing
post-deadline classification, so an ordinary early `httpx.ReadTimeout` remains
gateway unavailable.

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend/tests/runtime/test_runner_gateway_client.py::test_execution_absolute_guard_timeout_is_authoritative_before_next_clock_tick backend/tests/runtime/test_runner_gateway_client.py::test_client_maps_timeout_without_exposing_token backend/tests/runtime/test_runner_gateway_client.py::test_execution_request_enforces_absolute_slow_drip_deadline backend/tests/runtime/test_runner_gateway_client.py::test_completion_enforces_one_second_absolute_slow_drip_deadline backend/tests/runtime/test_runner_gateway_client.py::test_expired_execution_call_opens_no_transport_and_completion_uses_control_budget backend/tests/runtime/test_runner_gateway_client.py::test_completion_budget_is_capped_by_shorter_request_deadline backend/tests/runtime/test_runner_gateway_client.py::test_completion_does_not_widen_concurrent_execution_request_budget backend/tests/runtime/test_runner_gateway_client.py::test_execution_deadline_is_rechecked_after_response_validation backend/tests/runtime/test_sandbox_runtime.py::test_real_gateway_slow_drip_expiry_completes_as_sandbox_timeout -q --basetemp backend\.tmp-final-review-fix2-team-timeout-absolute-guard-green-20260904-a
```

Focused GREEN: `9 passed in 7.99s`.

Four concurrent copies of the real SandboxRuntime slow-drip node, using unique
basetemps `backend/.tmp-final-review-fix2-team-timeout-absolute-guard-stress-1`
through `-4-20260904-a`, produced `4/4 passed`. The identical pre-fix pressure
shape reproduced the classification failure in `1/4` copies.

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend/tests/runtime/test_runner_gateway_client.py -q --basetemp backend\.tmp-final-review-fix2-team-timeout-absolute-guard-client-module-green-20260904-a
```

Full client-module GREEN: `15 passed in 3.65s`.

A voluntary combined client/runtime sweep produced `70 passed` plus two
unrelated real-child-worker probe setup failures: under current host pressure,
the member and synthesis subprocesses did not enter their selected stage within
the fixture's 10-second startup allowance. An isolated rerun reproduced those
same setup failures. They do not execute the changed Runner Gateway client path;
the requested real slow-drip composition node passed in the focused gate and
all four concurrent stress copies. All pytest commands otherwise emitted only
the known shared `.pytest_cache` permission warning.

This correction changed `backend/app/runtime/runner_gateway_client.py` and
`backend/tests/runtime/test_runner_gateway_client.py` only. No other production
module was changed.
