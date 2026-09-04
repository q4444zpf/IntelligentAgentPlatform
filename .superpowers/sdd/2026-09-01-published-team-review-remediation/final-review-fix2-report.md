# Final Review Fix 2 Report

## Scope

This user-authorized second exceptional correction wave starts from
`4d391b6613218fd8c2ffae876b59443fdb482fe4` on
`codex/published-team-foundation`. It closes the three release-blocking
findings left after the first final-review fix wave:

1. a project-scoped Agent could be written into the platform-global default
   pointer through the public store API;
2. a fail-fast Team failure could not supersede a real
   `waiting_approval` Run, while a late approval could revive a terminal Run;
3. Team timeout enforcement did not bound in-flight runner I/O, member work,
   synthesis, terminal reporting, or late gateway persistence end to end.

No frontend or desktop file is changed by this wave. Frontend tests and build
were therefore not run, per the conditional acceptance instruction. No merge,
push, cleanup, or unrelated worktree change is included.

## Corrections

### Platform Agent default invariant

- `AgentStore.set_default_id()` delegates to the same transactional,
  eligibility-validating path as the ordinary default setter.
- Project-scoped, restricted-common, disabled, missing, and stale-version
  targets cannot become the platform-global default.
- Corrupt-pointer test setup now writes fixture state directly instead of
  retaining a public production bypass.

The focused RED was `1 failed, 72 deselected`. The focused default gate passed
`26` tests, and the complete affected Agent/conversation files passed
`102 passed, 2 skipped`. Its scoped review concluded Spec PASS and Quality
APPROVED with no P0/P1/P2 findings.

### Fail-fast approval race

- Failed and cancelled completion may atomically supersede
  `waiting_approval`; a late successful completion still may not.
- Coordinator finalization preserves the same terminal precedence.
- Approval resolution changes the Run only through a status-qualified
  `waiting_approval -> queued` update. Losing the race rolls back the Approval
  mutation and emits no queued event or resume dispatch.

The focused RED reproduced `5 failed, 42 deselected`. The focused GREEN
returned `5 passed`, and the complete three affected files returned `47`
tests with zero failures, errors, or skips. Its scoped review concluded Spec
PASS and Quality APPROVED with no actionable findings.

### Production-hard Team timeout

- The coordinator derives one immutable monotonic watchdog from the verified
  snapshot and bounds submit, status, recovery, polling sleep, terminate, and
  cleanup. Terminate and cleanup share one short control allowance.
- Workflow Runner, Launcher, and Runner Gateway propagate the earlier
  execution deadline across both HTTP hops and enforce one end-to-end async
  timeout in addition to phase timeouts.
- Expired or late Docker creation is rejected or force-removed before
  registration. Team planning, member, and synthesis work runs in public
  daemon threads that cannot keep the worker process alive.
- Execution deadline expiry maps authoritatively to `sandbox_timeout`; the
  separate bounded completion path retains gateway/control failure semantics.
- Checkpoint, event, model, Tool, capability, and artifact persistence recheck
  the Run under lock after external work, while exact idempotent replay remains
  valid.
- `_execute_team()` now declares its deadline as a monotonic `float`.

The first formal review found three P1 and one P2 issue. Correction Round 1
reported focused GREEN groups of `12`, `5`, `2`, `4`, and `1` tests, a
`290 passed` affected aggregate, and a real PostgreSQL serialization result of
`6 passed` with no skips. The scoped re-review concluded Spec PASS, Quality
APPROVED, all four findings ADDRESSED, and no open P0/P1/P2 issue.

Controller boundary verification independently returned:

- coordinator and two-hop deadline group: `16 passed`;
- gateway/runtime mapping group: `11 passed`;
- real child-worker and completion fallback: `4 passed`;
- PostgreSQL serialization wrapper: `6 passed` with no skips.

## Final-Acceptance Findings And Corrections

### Required E2E execution deadline fixture

The first Team E2E/security run returned `1 failed, 48 passed`: one direct
`RunExecutionRequest` fixture did not provide the newly required
`execution_deadline_at`. The fixture now assigns one explicit future value to
both request and execution deadlines. No production behavior or assertion was
changed.

The focused node passed, the implementer boundary rerun passed all `49` tests,
and the final controller rerun on the combined tree passed all `49` tests.

### Runner Gateway integration execution state

The first full backend run exposed twelve integration failures returning
`409 run_not_active`. The shared integration environment seeded Runs as
`pending`, although production transitions a Run to `running` before issuing
Runner Gateway work and the durable mutation fence correctly accepts only a
running Run.

The integration fixture now represents the production execution phase. The
normal-path test no longer repeats an artificial transition, and a dedicated
regression proves a pending Run rejects a new event without changing Run,
event, checkpoint, or idempotency-request state. No production code changed
for this correction.

The complete artifact, execution, and failure integration files passed:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend/tests/integration/test_runner_gateway_artifacts.py backend/tests/integration/test_runner_gateway_execution.py backend/tests/integration/test_runner_gateway_failures.py -q --basetemp backend\.tmp-final-review-fix2-integration-fixture-controller-green-20260904-a
```

Result: `28 passed, 1 warning in 10.67s`, exit `0`.

### Absolute-timeout classification on Windows

The first full backend run also exposed a load-sensitive classification race.
The client's outer `asyncio.timeout` raised its authoritative built-in
`TimeoutError`, but Windows' 15.625 ms monotonic clock resolution allowed the
following clock read to appear slightly before the deadline. The error was
therefore downgraded to `runner_gateway_unavailable`.

A deterministic regression reproduced the defect: `1 failed in 2.47s`, with
the next clock tick at `100.999` against deadline `101.0`. Built-in timeout
from the client's absolute guard is now handled separately: execution calls
map directly to `RunnerGatewayDeadlineExceeded`/`sandbox_timeout`, completion
calls remain `RunnerGatewayUnavailable`, and early ordinary `httpx.HTTPError`
continues to use the existing gateway-unavailable mapping.

The focused semantic matrix returned `9 passed`, four concurrent copies of
the formerly flaky slow-drip composition returned `4/4 passed`, and the full
Runner Gateway client module returned `15 passed`. Controller verification of
the full client module plus the formerly failing runtime composition returned:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend/tests/runtime/test_runner_gateway_client.py backend/tests/runtime/test_sandbox_runtime.py::test_real_gateway_slow_drip_expiry_completes_as_sandbox_timeout -q --basetemp backend\.tmp-final-review-fix2-timeout-classification-controller-green-20260904-a
```

Result: `16 passed, 1 warning in 8.16s`, exit `0`.

The real child-worker/fallback boundary was rerun without concurrent load and
returned `4 passed, 1 warning in 33.67s`. This resolves two earlier voluntary
stress-sweep setup misses where child processes did not enter the selected
stage within the test fixture's ten-second startup allowance.

## Final Verification

### Full backend

The first final-tree run was intentionally retained as failure evidence:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend -q --basetemp backend\.tmp-final-review-fix2-full-backend-20260904-a
```

Result: `13 failed, 1099 passed, 48 skipped, 3 warnings in 817.34s`.
Twelve failures were the stale integration Run state, and one was the Windows
absolute-timeout classification race described above.

After the two minimal corrections, the final stable-tree run was:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest backend -q --basetemp backend\.tmp-final-review-fix2-full-backend-20260904-b
```

Result: `1114 passed, 48 skipped, 2 warnings in 800.26s (0:13:20)`, exit `0`.
The warnings are the existing Authlib `authlib.jose` deprecation and the
ACL-restricted shared `.pytest_cache` warning.

### Team E2E and security boundaries

```powershell
$env:PYTHONPATH='backend'; .\.testvenv-task5\Scripts\python.exe -m pytest tests/e2e/test_published_team_run.py backend/tests/runtime/test_runner_gateway_nginx_boundary.py backend/tests/collaboration backend/tests/identity/test_authorization.py -q --basetemp backend\.tmp-final-review-fix2-team-e2e-security-final-20260904-d
```

Result: `49 passed, 1 warning in 10.79s`, exit `0`. The warning is the known
shared pytest-cache permission warning.

### Frontend and diff integrity

No frontend or desktop file is present in the correction diff, so frontend
tests, type checking, and production build were not run.

`git diff --check` exits `0` with no whitespace errors. Git emits only expected
Windows LF-to-CRLF working-copy notices.

## Integration Boundary

Only scoped source, tests, the real PostgreSQL concurrency regression, and
correction reports are intended for this commit. Virtual environments,
downloaded dependencies, pytest basetemps, logs, JUnit XML, temporary scripts,
build output, review packages, and unrelated pre-existing changes are
excluded. This wave will be committed but not merged or pushed; one final
combined scoped review remains controller work.
