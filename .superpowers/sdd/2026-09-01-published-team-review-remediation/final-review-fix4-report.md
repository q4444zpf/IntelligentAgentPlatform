# Final Review Fix 4 Report

## Status

Implementation and required verification are complete. Independent scoped
review is pending at the time of this commit report.

## Finding verification and root cause

- `_lock_admitted_run()` locks and refreshes the active Run, loads and verifies
  the immutable Team snapshot, derives its absolute deadline, and checks that
  deadline.
- `_admit_approved_transport()` then closes that read transaction with
  `session.rollback()`. Before this correction it returned the captured
  deadline without checking the clock again.
- A rollback that crosses the deadline therefore returned stale admission.
  The builtin executor could begin immediately afterward, and the Team-only
  MCP boundary callback had the same gap before `call_tool()`.
- The terminal application-clock and database-time guards rejected late
  success persistence, but they could not undo an external side effect that
  had already begun.

The deterministic tests wrap the real session rollback, observe the Gateway
transaction as open before the real rollback and closed afterward, and advance
the injected clock after the real rollback but before the wrapper returns.
This isolates the exact blocking-operation interval without a wall-clock sleep.

## Strict TDD evidence

### RED

The first draft RED returned `2 failed, 3 passed in 12.01s`, but the builtin
test also counted the later terminal rollback. The test bookkeeping was
tightened before any production edit so it observed only the final admission
rollback.

Definitive RED command against unchanged
`806d35a452f433e409d82990d24f2077c643bd5a`:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/tools/test_gateway.py::test_approved_team_builtin_crossing_deadline_during_admission_rollback_stops_before_executor backend/tests/tools/test_gateway.py::test_approved_team_mcp_crossing_deadline_during_final_admission_rollback_stops_before_transport backend/tests/tools/test_gateway.py::test_approved_agent_tool_without_snapshot_executes_after_digest_check backend/tests/tools/test_gateway.py::test_approved_agent_mcp_tool_remains_snapshot_optional_during_setup --basetemp=backend/.tmp-final-review-fix4-red-20260904-b
```

Result: `2 failed, 3 passed in 11.02s`.

- Builtin failure: `external_calls` was `[False]` instead of `[]`. The executor
  began after the rollback and with no open Gateway transaction.
- MCP failure: the protocol fake recorded one contract-valid `call_tool()`
  instead of zero calls.
- Both rollback probes observed `[True, False]`, proving that the deadline
  crossed within the transaction-closing rollback wrapper.
- Both status variants of the legacy Agent builtin case and the legacy Agent
  MCP case passed on the unchanged baseline.

### Minimal implementation

Immediately after `session.rollback()` completes,
`_admit_approved_transport()` now calls the existing centralized
`_require_before_deadline(deadline)` before returning admission. No additional
transaction, snapshot rule, transport callback, exception mapping, or terminal
persistence behavior was introduced.

### GREEN

Exact focused GREEN command:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/tools/test_gateway.py::test_approved_team_builtin_crossing_deadline_during_admission_rollback_stops_before_executor backend/tests/tools/test_gateway.py::test_approved_team_mcp_crossing_deadline_during_final_admission_rollback_stops_before_transport backend/tests/tools/test_gateway.py::test_approved_agent_tool_without_snapshot_executes_after_digest_check backend/tests/tools/test_gateway.py::test_approved_agent_mcp_tool_remains_snapshot_optional_during_setup --basetemp=backend/.tmp-final-review-fix4-green-20260904-a
```

Result: `5 passed in 9.99s`.

The two Team cases now raise `sandbox_timeout` after the final rollback and
before external entry. Their durable Tool state remains `started`, with no
result, completion time, duration, or error. Agent deadlines remain `None`, so
the added check is a no-op and legacy snapshot-optional execution remains
compatible.

## Required regression evidence

Complete Tool Gateway module:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider backend/tests/tools/test_gateway.py --basetemp=backend/.tmp-final-review-fix4-tool-gateway-green-20260904-a
```

Result: `46 passed in 82.95s`.

Complete execution-snapshot module:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider backend/tests/runtime/test_execution_snapshot.py --basetemp=backend/.tmp-final-review-fix4-snapshot-green-20260904-a
```

Result: `18 passed in 3.78s`.

Approval resume plus Runner/Gateway replay, idempotency, HTTP mapping,
listener/state, and compensation contracts:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider backend/tests/conversations/test_dispatcher.py backend/tests/runtime/test_runner_gateway_state.py backend/tests/runtime/test_runner_gateway_api.py backend/tests/runtime/test_gateway_tools.py backend/tests/integration/test_runner_gateway_failures.py backend/tests/integration/test_runner_gateway_execution.py --basetemp=backend/.tmp-final-review-fix4-contracts-green-20260904-a
```

Result: `71 passed in 34.49s`.

Expanded affected aggregate:

```powershell
.\.testvenv-task5\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider backend/tests/conversations/test_dispatcher.py backend/tests/runtime/test_execution_snapshot.py backend/tests/runtime/test_launcher_client.py backend/tests/runtime/test_workflow_runner.py backend/tests/runtime/test_gateway_model.py backend/tests/runtime/test_gateway_tools.py backend/tests/runtime/test_sandbox_runtime.py backend/tests/runtime/test_runner_gateway_state.py backend/tests/runtime/test_runner_gateway_api.py backend/tests/integration/test_runner_gateway_execution.py backend/tests/integration/test_runner_gateway_failures.py backend/tests/tools/test_gateway.py backend/tests/mcp/test_protocol_client.py backend/tests/mcp/test_credentials.py backend/tests/test_mcp.py --basetemp=backend/.tmp-final-review-fix4-affected-green-20260904-a
```

Result: `278 passed in 236.47s`, zero skips and zero failures.

Full live PostgreSQL concurrency/deadline module:

```powershell
$env:PATH = "I:\智能体平台\IntelligentAgentPlatform\.worktrees\published-team-foundation\.testvenv-task5\Scripts;$env:PATH"
& .\backend\tests\support\run_postgres_tests.ps1 -PytestPath 'backend/tests/integration/test_runner_gateway_state_concurrency.py'
```

The first sandboxed invocation could not access the Windows Docker named pipe
and exited before tests ran. The approved Docker-daemon rerun used the exact
same command and returned `11 passed in 20.03s`, zero skips. The disposable
container id was printed and the wrapper exited successfully.

Whitespace verification:

```powershell
git diff --check
```

Result: exit code `0`; Git emitted only existing LF-to-CRLF conversion notices.

## Changed files

- `backend/app/tools/gateway.py`
- `backend/tests/tools/test_gateway.py`
- `.superpowers/sdd/2026-09-01-published-team-review-remediation/final-review-fix4-report.md`

No dependency directory, basetemp, log, cache, build output, or other
pre-existing untracked artifact belongs to the correction scope.

## Contract preservation and residual risk

- The post-rollback check uses the deadline already derived from the locked,
  digest-verified immutable Team snapshot. Existing missing or invalid Team
  snapshots still fail closed, including the MCP boundary revalidation.
- The check runs only after rollback, so builtin executors and MCP transports
  still begin without an open Gateway transaction.
- Legacy Agent approvals still produce `deadline is None`; their builtin and
  MCP execution behavior remains unchanged and snapshot-optional.
- Admission remains cooperative. There is an irreducible instruction interval
  after the final clock comparison and before an external function begins, and
  an entered external call cannot be recalled. Broadening into that post-check
  race is outside this authorized finding. Independent terminal application-
  clock and database-time guards still reject late successful persistence.
