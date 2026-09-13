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
