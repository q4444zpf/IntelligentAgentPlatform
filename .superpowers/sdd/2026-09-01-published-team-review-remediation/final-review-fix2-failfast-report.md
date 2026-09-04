# Final Review Fix 2: Fail-Fast Approval Race Report

## Root-Cause Confirmation

The focused regression command reproduced the documented RED state before
production changes: `5 failed, 42 deselected`.

The failures came from three independent state-transition guards:

1. `RunnerGatewayService.complete()` rejected every non-interrupted
   completion while a Run was `waiting_approval`, so a fail-fast `failed`
   completion could not terminate the Run.
2. `SandboxRunCoordinator._finish()` excluded `waiting_approval` for every
   terminal state by default, so a later failed container exit could not
   supersede the pending approval state.
3. The approval router changed the Approval to `approved` and then assigned
   the Run to `queued` unconditionally. A terminal Run therefore accepted a
   late approval, produced queued events, and dispatched a resume.

## Production Changes

- `backend/app/runtime/runner_gateway_service.py`
  - Keep terminal-run conflicts and the `waiting_approval -> completed`
    conflict, while allowing `failed` and `cancelled` completion requests to
    terminate a waiting Run.
- `backend/app/runtime/run_lifecycle.py`
  - Preserve waiting-approval protection only for a successful `completed`
    outcome. Failed and cancelled terminal outcomes now use the same atomic
    status-qualified update and can supersede `waiting_approval`.
- `backend/app/approvals/router.py`
  - Replace the unconditional queued assignment with an atomic
    `UPDATE ... WHERE status = 'waiting_approval'`. A zero-row transition
    raises `ApprovalConflictError`; the existing exception handler rolls back
    the earlier Approval mutation and audit write before any queued events or
    dispatcher resume are reached.

No Agent-default or Team-timeout files were changed by this correction.

## Verification

RED reproduction, run from `backend/`:

```powershell
..\.testvenv-task5\Scripts\python.exe -m pytest tests\integration\test_runner_gateway_failures.py tests\runtime\test_run_lifecycle.py tests\approvals\test_api.py -q -k "fail_fast_failure_supersedes_real_waiting_approval_state or failed_container_exit_supersedes_waiting_approval or approve_cannot_resume_terminal_run" --basetemp .tmp-final-review-fix2-failfast-red-repro-20260904-b
```

Output: `5 failed, 42 deselected, 2 warnings in 5.61s`.

Focused GREEN, run from `backend/`:

```powershell
..\.testvenv-task5\Scripts\python.exe -m pytest tests\integration\test_runner_gateway_failures.py tests\runtime\test_run_lifecycle.py tests\approvals\test_api.py -q -k "fail_fast_failure_supersedes_real_waiting_approval_state or failed_container_exit_supersedes_waiting_approval or approve_cannot_resume_terminal_run" --basetemp .tmp-final-review-fix2-failfast-green-20260904-b
```

Output: `5 passed, 42 deselected, 1 warning in 4.61s`.

Complete affected-file GREEN, run from `backend/` with a fresh basetemp:

```powershell
..\.testvenv-task5\Scripts\python.exe -m pytest tests\integration\test_runner_gateway_failures.py tests\runtime\test_run_lifecycle.py tests\approvals\test_api.py -q --basetemp .tmp-final-review-fix2-failfast-affected-20260904-d --junitxml .tmp-final-review-fix2-failfast-affected-20260904-d-results.xml
```

The generated JUnit result reports `tests="47"`, `failures="0"`,
`errors="0"`, `skipped="0"`, completed in `58.970` seconds.

## Self-Review

- Terminal Runs remain rejected by the Runner Gateway before any completion
  event or status write.
- A waiting Run still rejects a late successful completion, but accepts
  failed and cancelled terminal outcomes.
- Approval resolution writes `queued` only after the persisted status
  comparison succeeds in the same transaction.
- A failed status-qualified update raises the existing conflict error; the
  existing rollback occurs before events or resume dispatch, preserving the
  approval as pending.
- Existing idempotency and successful approval-resume behavior are covered by
  the complete affected-file suite.

## Concerns

Pytest emitted cache-write warnings because the shared worktree's
`.pytest_cache` directory is permission-restricted. The tests themselves
completed successfully; no production-code concern was found. No files were
staged or committed.
