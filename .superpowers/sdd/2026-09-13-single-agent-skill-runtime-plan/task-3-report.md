# Task 3 Report: Script Declaration and Controlled Runner Execution

Implemented declared Skill script parsing/execution and Runner Gateway admission.

## TDD Evidence

- RED: `python -m pytest backend/tests/runtime/test_skill_scripts.py -q` initially failed during collection with `ModuleNotFoundError: No module named 'app.runtime.skill_scripts'`.
- GREEN: with bundled dependencies and writable temp directory, `pytest backend/tests/runtime/test_skill_scripts.py backend/tests/runtime/test_gateway_tools.py backend/tests/runtime/test_runner_gateway_skill_resources.py -q` => **27 passed, 1 warning**.
- Regression: `pytest backend/tests/runtime/test_sandbox_runtime.py -x -q` => **62 passed, 1 existing environment failure** in `test_real_worker_process_does_not_join_abandoned_team_deadline_work[planning]`; its spawned subprocess did not create the readiness file because the test interpreter/dependency environment is unavailable outside the bundled test process.

## Files

- Added `backend/app/runtime/skill_scripts.py` and `backend/tests/runtime/test_skill_scripts.py`.
- Extended gateway tools, sandbox runtime, Runner Gateway schemas/router/service/client, run-token actions, and run lifecycle token action set.

## Notes

Scripts require strict relative declared paths, object-only JSON schemas, bounded timeout/output, cancellation, non-zero exit handling, `sys.executable`, `shell=False`, and a minimal environment. Script tools share the normal model tool list/budget and request a `skill.script.execute` Gateway lease before local Runner execution.

## Fix Round 1

Addressed review findings: initialize the resource workspace for no-Skill runs, reject drive-prefixed paths, require exact deterministic script names, add duplicate call admission keys, propagate a runtime cancellation event, map approval responses to `RunnerApprovalInterruption`, and merge bounded process output into a temporary file to cap captured stdout/stderr.

Verification: `pytest --basetemp=backend/.tmp/task3-fix backend/tests/runtime/test_skill_scripts.py backend/tests/runtime/test_gateway_tools.py backend/tests/runtime/test_runner_gateway_skill_resources.py -q` => **27 passed, 1 warning**. Sandbox regression excluding the known external worker probe: `pytest --basetemp=backend/.tmp/task3-fix backend/tests/runtime/test_sandbox_runtime.py -k 'not real_worker_process' -q` => **63 passed, 3 deselected**.

## Fix Round 2

Implemented durable `ToolInvocation`/`Approval` persistence with matching-argument approved resume, strict lease response validation, idempotent terminal completion route/client callbacks, start/terminal runtime audit records, remaining-run-deadline enforcement, and separate stdout/stderr bounded files with overflow termination. Added regression coverage for durable approval resume, malformed lease rejection, terminal completion callbacks, stderr diagnostics, and remaining-deadline timeout.

Verification: `pytest --basetemp=backend/.tmp/task3-r2f backend/tests/runtime/test_skill_scripts.py backend/tests/runtime/test_gateway_tools.py backend/tests/runtime/test_runner_gateway_skill_resources.py -q` => **29 passed, 1 Starlette deprecation warning**. Sandbox regression from round 1 remains **63 passed, 3 worker-probe tests deselected**; the worker-probe environment limitation is unchanged and unrelated to Task 3.

## Fix Round 3

Updated the production approval dispatcher so approved declared-script invocations resume the sandbox without entering ordinary `ToolGateway.execute_approved`. Made lease completion terminal and immutable, added final separate stderr bound checks, converted unexpected executor errors to failed terminal callbacks and safe tool errors, and added a real-time deadline watcher that sets the runner cancellation event without consuming injected monotonic clocks.

Verification: focused script/Gateway/resource tests => **30 passed, 1 known Starlette deprecation warning**; approvals and dispatcher tests => **20 passed, 1 known warning**; sandbox runtime excluding the three environment-dependent real-worker probes => **63 passed, 3 deselected**. The initial sandbox run found two injected-clock regressions caused by the watcher consuming the fake clock; after fixing the watcher both focused failures passed and the 63-test sandbox regression passed.

## Fix Round 4

Addressed all three remaining Important findings from `task-3-rereview-round3.md`:

- Script completion now acquires the Run row lock before reading idempotency state and uses a guarded `running`-to-terminal database update. The shared `finish_script_invocation` helper emits the terminal event and audit only for the winning transition and refreshes stale ORM state for losing callbacks. Tests use two concurrent sessions holding stale running leases and cover both matching and conflicting completions.
- `SandboxRunCoordinator` now finalizes running declared-script leases as cancelled, with terminal timestamps, safe error code, event, and audit, inside the same transaction that cancels the Run. Token revocation still precedes container termination. Container termination gives Docker a one-second SIGTERM grace period before SIGKILL; `run_worker` binds SIGTERM to `SandboxRuntime.cancel`, which sets the execution event so an active script exits promptly. The coordinator owns durable cancellation even when a revoked token prevents the runner's final callback. The worker restores the previous signal handler on exit.
- Each execution captures its own cancellation event and has a separate watcher-stop event. An outer `finally` stops and joins the deadline watcher on every return, including expiry, invalid snapshot, gateway failure, and normal completion. A reused-runtime regression verifies an earlier deadline cannot cancel the next execution.

TDD evidence: the two new regression files initially produced **10 failed** before implementation. Nine failures directly reproduced the reported behavior; the live-script test's resource-response fixture was then corrected to match the real Gateway contract. With the cancellation hook disabled in an isolated test process, that corrected test failed with worker exit 1 instead of cancelled exit 3. With the implementation enabled, both new files passed: **10 passed, 1 known Starlette deprecation warning**.

Final focused verification (exit 0, 152.88 seconds):

```text
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -v "I:\智能体平台\IntelligentAgentPlatform:/workspace" -w /workspace intelligent-agent-platform-api:local python -m pytest --basetemp=/tmp/task3-r4-regression -p no:cacheprovider backend/tests/runtime/test_skill_scripts.py backend/tests/runtime/test_gateway_tools.py backend/tests/runtime/test_runner_gateway_skill_resources.py backend/tests/approvals/test_api.py backend/tests/conversations/test_dispatcher.py backend/tests/runtime/test_sandbox_runtime.py backend/tests/runtime/test_run_lifecycle.py backend/tests/runtime/test_container_launcher.py backend/tests/runtime/test_run_tokens.py backend/tests/runtime/test_script_terminal_lifecycle.py backend/tests/runtime/test_script_runtime_cancellation.py -k "not real_worker_process" -q
194 passed, 3 deselected, 1 warning
```

The warning is the existing Starlette import of deprecated `anyio.abc.BlockingPortal`. The three existing real-worker environment probes remain explicitly deselected; the new cancellation test does execute a real script subprocess. No database schema or token permission changes were required. Existing Task 1/2 work and unrelated user changes are preserved.
