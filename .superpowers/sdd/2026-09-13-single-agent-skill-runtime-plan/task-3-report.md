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
