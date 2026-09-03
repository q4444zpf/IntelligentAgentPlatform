# Final Review Fix 2: Agent Default Store Invariant Report

## RED

Command:

```text
.testvenv-task5\Scripts\python.exe -m pytest -vv backend/tests/test_agents.py -k "set_default_id_rejects_project_agent_as_platform_default_atomically" --basetemp=backend/.tmp-final-review-fix2-agent-default-red-20260904-a
```

Observed result: **1 failed, 72 deselected**. The direct `set_default_id`
call accepted the project-scoped Agent, producing the expected
`Failed: DID NOT RAISE <class 'ValueError'>` failure.

## GREEN

Focused Agent/default gate:

```text
.testvenv-task5\Scripts\python.exe -m pytest -q backend/tests/test_agents.py -k "default" --basetemp=backend/.tmp-final-review-fix2-agent-default-green-20260904-c
```

Result: **26 passed, 47 deselected**.

Fixture follow-up gate:

```text
.testvenv-task5\Scripts\python.exe -m pytest -q backend/tests/test_agents.py backend/tests/conversations/test_api.py -k "service_delegates_mutations_to_atomic_store_operations or message_agent_selection_uses_persisted_scope_and_common_wildcard" --basetemp=backend/.tmp-final-review-fix2-agent-default-fixture-green-20260904-a
```

Result: **2 passed, 102 deselected**.

Complete affected-file gate:

```text
.testvenv-task5\Scripts\python.exe -m pytest -q backend/tests/test_agents.py backend/tests/conversations/test_api.py --basetemp=backend/.tmp-final-review-fix2-agent-default-affected-20260904-b
```

Result: **102 passed, 2 skipped**.

## Files Changed

- `backend/app/agents/store.py`
- `backend/tests/test_agents.py`
- `backend/tests/conversations/test_api.py`
- `.superpowers/sdd/2026-09-01-published-team-review-remediation/final-review-fix2-agent-default-fix-report.md`

## Self-Review

- `set_default_id` now delegates to `set_default_agent`, so it shares the
  existing pointer lock, target lock, eligibility validation, compare-and-swap,
  not-found handling, and atomic transaction.
- The new direct-store regression asserts a project Agent is rejected and that
  the platform pointer remains unchanged.
- Legacy corrupt-pointer tests now update `PlatformSettingRecord.value`
  directly inside their own test transactions; production code no longer
  provides that fixture bypass.
- The mutation spy clears setup-time builtin repair observations before
  asserting service mutation calls.

## Concerns

Pytest emitted its existing `PytestCacheWarning` because this worktree's
`.pytest_cache` is not writable. It did not affect test outcomes. No fail-fast
or timeout files were changed.
