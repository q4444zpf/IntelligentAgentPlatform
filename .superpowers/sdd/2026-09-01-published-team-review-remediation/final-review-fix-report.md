# Final Review Fix Report

## Scope

This remediation starts from
`7eaac8761fb633b3366c03efd634436cf98bd4e2` on
`codex/published-team-foundation`. It addresses the final review findings in
one bounded correction wave. No integration, merge, push, or unrelated
cleanup was performed.

## Corrections

- Team Tool call IDs are namespaced by the persisted task or synthesis
  invocation ID. IDs longer than the gateway limit use a deterministic
  SHA-256 form, and the same final ID feeds the idempotency key.
- Team checkpoint recovery fails closed when a checkpoint is present but its
  state is missing, malformed, inconsistent, or belongs to another state
  kind. A persisted fail-fast member failure cannot resume pending work.
- A fail-fast member failure wins over a parallel approval interruption, and
  the Team execution deadline is the earlier of the request deadline and the
  published Team timeout.
- Agent mutation, copy, and delete operations authorize against the persisted
  Agent scope under the mutation transaction. Project Agents require the
  matching scope; common Agents require a unit administrator.
- Team Tool invocation and approved-Tool resume reload the current user,
  unit, project, membership, role, and permission state immediately before
  side effects. Single-Agent execution keeps its accepted-run role snapshot.
- Model provider calls run outside the gateway database transaction. A
  committed pending reservation protects idempotency while the provider is in
  flight, returns a stable conflict to duplicates, and is removed after a
  provider failure before the failure audit is recorded.
- Empty or partial persisted Team drafts are normalized into a complete
  editable frontend form before rendering or validation.
- The frontend test script caps Vitest at four workers. On the 20-worker
  verification host this removes cold-start CPU contention without extending
  individual test timeouts.
- The root Team E2E gateway fake now represents an absent checkpoint with the
  production client's explicit `checkpoint_not_found` error instead of raw
  `None`, preserving the fail-closed production contract.

## Test-First Evidence

- Runtime Tool-call namespace mutation: removing the invocation namespace
  produced the expected RED failure; restoring it returned GREEN.
- Model reservation mutations: removing reservation creation produced RED;
  removing pending-response handling produced RED; the restored focused gate
  passed `2` tests.
- The explicit Team E2E initially failed with
  `team_recovery_required`, proving the stale fake exercised the new recovery
  boundary. After the minimal fake correction, the exact test passed, then
  the complete security boundary set passed.
- Focused backend aggregate: `149 passed, 2 skipped`.
- Focused frontend Team coverage: `11 passed`.
- Focused live Team authorization and approval-resume coverage: `7 passed`.
- Focused model transaction coverage: `5 passed`.

## Final Verification

### Backend

```powershell
$env:PYTHONPATH='backend'
.\.testvenv-task5\Scripts\python.exe -m pytest backend -q --basetemp backend/.tmp-final-review-full-backend-20260903
```

Final pre-commit result: `1045 passed, 42 skipped, 2 warnings in 739.89s`,
exit `0`.
The warnings are Authlib's existing `authlib.jose` deprecation and the known
ACL-restricted `.pytest_cache` path.

### Frontend

```powershell
& 'C:\Program Files\nodejs\npm.cmd' test -- --run
```

The uncapped command was load-sensitive: one run timed out one cold-start test
and a later final run timed out two cold-start tests just above the 5-second
default. The original target passed in isolation (`4 passed`; `898ms`). A
diagnostic run capped at four workers passed all `303` tests and reduced the
affected cold starts to about one second, confirming worker contention on the
20-worker host. The package test script now applies that cap without relaxing
test timeouts. The exact required command then passed `40` files and `303`
tests in `69.32s`, exit `0`.

```powershell
& 'C:\Program Files\nodejs\npm.cmd' run build
```

Final result: `vue-tsc --noEmit` and Vite production build passed, exit `0`;
Vite transformed `6976` modules, built in `1m 10s`, and emitted only its
existing large-chunk advisory.

### Team E2E And Security Boundaries

```powershell
$env:PYTHONPATH='backend'
.\.testvenv-task5\Scripts\python.exe -m pytest tests/e2e/test_published_team_run.py backend/tests/runtime/test_runner_gateway_nginx_boundary.py backend/tests/collaboration backend/tests/identity/test_authorization.py -q --basetemp backend/.tmp-final-review-security-green-20260903
```

Final result after the E2E fake correction: `49 passed, 1 warning in 17.97s`,
exit `0`. The warning is the known ACL-restricted pytest cache.

### Diff Integrity

`git diff --check` exited `0` with no whitespace errors. Git printed only
line-ending conversion notices. Generated dependencies, virtual environments,
logs, test databases, pytest temporary directories, and frontend build output
are excluded from the commit.

## Residual Notes

No failing verification gate remains. Non-blocking existing concerns are the
Authlib deprecation warning, the ACL-restricted pytest cache warning, Vite's
large-chunk advisory, and the additional elapsed time introduced by capping
Vitest concurrency for deterministic runs.
