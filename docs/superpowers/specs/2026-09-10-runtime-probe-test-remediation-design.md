# Runtime Probe Test Remediation Design

## Context

The Project Skill production-activation branch is functionally accepted, but its final backend regression is not green on Windows. Three parameterizations of `test_real_worker_process_does_not_join_abandoned_team_deadline_work` fail before the worker reaches the selected slow stage. The child process currently executes `runpy.run_path()` against the complete `test_sandbox_runtime.py` module before it can write the stage-ready marker. Measured module loading takes 42-44 seconds on this workstation, while the test allows 10 seconds for stage readiness.

The same final gate also found one branch-owned Black formatting difference in `backend/app/skills/project_startup.py`. Four other Black failures and four Ruff `I001` findings already exist at the plan base and are not part of this remediation.

## Goal

Make the worker deadline test measure production deadline behavior instead of whole-test-module bootstrap time, without weakening its stage-ready or termination deadlines. Format the one new Project Skill startup module, then rerun the affected and complete branch gates.

## Scope

- Replace the child process dependency on `runpy.run_path(test_sandbox_runtime.py)` with a small test-only probe module.
- Preserve the existing planning, member, and synthesis cases and their real `run_worker.main()` execution.
- Preserve the 10-second deadline for entering the selected slow stage and the 2.5-second deadline for abandoning that stage after the execution deadline.
- Format only `backend/app/skills/project_startup.py` with Black.
- Update the durable Project Skill verification report with the final evidence.

No production runtime behavior, API behavior, database schema, object storage, frontend, deployment configuration, or remote environment changes are allowed.

## Design

### Lightweight Probe Boundary

Create a focused helper under `backend/tests/runtime/` that can be launched as a Python child process. It owns only the fixture construction required for the deadline test:

- a schema-v5 team execution snapshot and matching execution request serialized by the parent test;
- a minimal gateway implementing the methods exercised by `SandboxRuntime`;
- minimal plan and text graphs;
- the stage-selecting agent factory;
- wiring `run_worker` to the request, gateway, and `SandboxRuntime`;
- writing the supplied ready file only when the selected planning, member, or synthesis graph actually begins its slow `invoke()` call.

The helper accepts the stage, ready-file path, snapshot JSON path, and request JSON path as command-line arguments. It does not import or execute `test_sandbox_runtime.py`, does not expose secrets, and does not add production interfaces. The parent test continues to own fixture serialization, process creation, bounded waiting, termination, stdout/stderr capture, temporary paths, and the assertions on exit code and elapsed time.

### Timing Semantics

The existing time budgets remain unchanged:

- at most 10 seconds for the child to reach the selected slow production stage;
- a 0.75-second production execution window, bound in the child after helper imports, JSON validation, and test wiring complete;
- at most 2.5 seconds after the ready marker for the worker to exit with code 4;
- bounded kill/wait cleanup in `finally`.

Immediately before invoking real `run_worker.main()`, the child replaces only the serialized request's timing fields with `deadline_at = now + 5 seconds` and `execution_deadline_at = now + 0.75 seconds`. This matches the old inline probe, which constructed its request after child imports and test-module loading, and prevents interpreter/import latency from consuming the execution window. No production clock is replaced. This keeps the regression sensitive to worker deadline handling while removing unrelated process-bootstrap cost, rather than increasing a timeout until the current machine happens to pass.

### Formatting Remediation

Run Black only on `backend/app/skills/project_startup.py`. The expected change is mechanical formatting of the Alembic head-loading expression. No logic, exception boundary, or public interface changes are permitted.

## Testing

Follow TDD for the probe remediation:

1. Record the existing three-parameter failure on the current test harness.
2. Add the lightweight probe and switch the parent test to it.
3. Run the three parameters and require all to pass with the original timing assertions.
4. Run the complete runtime test directory sequentially.
5. Run the complete backend suite with external `TEST_*` cleared and the approved dedicated Cookie test database.
6. Run the dedicated real PostgreSQL/MinIO selections with zero configuration skips.
7. Run project virtual-environment `pip check`, branch-scoped Ruff with documented baseline `I001` handling, Black checks, `git diff --check`, Compose validation, image build/command inspection, and protected-path comparison.

The probe test must fail if the worker joins the abandoned slow stage, misses the selected stage, returns the wrong exit code, or exceeds the existing termination budget. Tests must remain sequential where process timing is involved.

## Failure Handling

- Every child-process wait remains bounded, and cleanup kills only the process created by the current test.
- A probe import or fixture error is reported through captured stdout/stderr instead of being converted into a timing success.
- If the original runtime failure persists, collect the exact child error before changing timeouts or production code.
- If the complete suite exposes a new failure outside this remediation, stop and diagnose it separately; do not widen this change opportunistically.

## Alternatives Rejected

- Raising the ready timeout to 60 seconds would make the suite slower and bind correctness to workstation performance without separating bootstrap from the behavior under test.
- Adding a second bootstrap marker would distinguish phases but still load the entire test module in every child and retain an arbitrary environment-dependent startup budget.
- Modifying production runtime startup to accommodate the test would change the wrong boundary and is outside scope.

## Delivery

Use the existing isolated branch `codex/project-skill-production-activation`. Commit the design, implementation, tests, formatting change, and final verification evidence in focused commits. Run an independent task review and final verification before offering merge or push options. Do not merge, push, deploy, or remove the worktree without a later user choice.
