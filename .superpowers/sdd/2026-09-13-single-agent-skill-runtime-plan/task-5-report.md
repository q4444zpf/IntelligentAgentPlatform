# Task 5: Single-Agent Skill Tool/MCP Acceptance

Status: **DONE** after formal fix round 1 and controller acceptance. Current Skill availability at resource-read time is rechecked; the targeted RED/GREEN and corrected-tree regressions passed, independent rereview found no actionable issue, and the clean isolated tree passed browser, frontend-build and complete-backend gates. Base commit: `95f1235`.

## Implementation and boundaries

- Added `backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py` with a real `SandboxRuntime`, default `DeepAgentFactory`, installed Deep Agents graph, `GatewayChatModel`, LangGraph tool loop, `GatewayStructuredTool`, signed RunToken, HTTP Runner Gateway, real snapshot/Skill publication repositories, package validation/materialization, script subprocess and persisted messages/events/audits. Test configuration supplies an in-memory Agent definition; the Skill publication and exact version binding are real database records. Only remote completion, MCP transport and S3 client are deterministic external boundaries.
- Positive acceptance uses a package with display version `2.7.0` and publication sequence `1`, frozen instructions, `references/rules.txt`, `templates/result.json`, declared `scripts/normalize.py`, `system.get_current_time` and registered `mcp.water.level`. Five actual tool calls return to the model and the final assistant message is persisted. An unauthorized resource attempt in the same run provides failure audit evidence while authorized work still completes.
- Added `skill.<name>.resource.read({path})`, with a manifest enum, exact membership guard, UTF-8 decoding and returned-byte digest verification. It uses the existing resource Gateway, participates in the existing model tool budget and does not alter ArtifactBackend. Framework filesystem/task tools are allowed alongside platform tools.
- Fixed published package validation to use frozen version/package identity; legacy snapshots retain their display-version consistency check. A database publication sequence is no longer compared with a package display string.
- Added required production resource repository/audit dependencies and `skill.resource.read/failed` persistence. Read failures never silently skip auditing. Added idempotent `runner.run.completed/failed/cancelled` audit at the completion transaction.
- Closed the deferred Task 3 lease finding. Coordinator timeout/failure/cancellation and Gateway terminal callbacks share `finish_running_script_invocations`, retain the run lock, end all running declared-script leases before credential revocation, and use the existing conditional terminal transition to emit one stable script terminal event/audit.

## TDD evidence

All Docker test commands below use the already installed local API image. The Windows Docker daemon required sandbox escalation; an initial non-elevated `docker images` read returned access denied. No platform database or object bucket was reset.

Command prefix for the initial standard runs (the exact arguments follow in the table):

```powershell
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -v "I:\智能体平台\IntelligentAgentPlatform:/workspace" -w /workspace intelligent-agent-platform-api:local python -m pytest
```

| Arguments after the exact prefix | Exit / evidence |
| --- | --- |
| `--basetemp=/tmp/task5-red -p no:cacheprovider backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py -q` | Exit 1: 2 failed, 1 passed, 1 warning, 13.29s. Timeout/failed tried to revoke token while script lease was still `running`; cancellation was passing characterization. |
| `--basetemp=/tmp/task5-green -p no:cacheprovider backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py backend/tests/runtime/test_script_terminal_lifecycle.py -q` | Exit 0: 6 passed, 1 warning, 20.23s. Includes stale concurrent script completions and cancellation. |
| `--basetemp=/tmp/task5-positive-red-clean -p no:cacheprovider backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py -k published_skill -q` | Exit 1: 1 failed, 3 deselected, 1 warning, 11.00s. Genuine RED: `skill_resource_invalid` before first completion because display `2.7.0` differed from publication `1`. |
| `--basetemp=/tmp/task5-resource-red -p no:cacheprovider backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py -k published_skill -q` | Exit 1: 1 failed, 3 deselected, 1 warning, 11.76s. After the identity fix, the real graph ran but the model tool set omitted `skill.forecast.resource.read`. |
| `--basetemp=/tmp/task5-audit-red -p no:cacheprovider backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py -k "published_skill or persists" -q` | Exit 1: 3 failed, 3 deselected, 1 warning, 14.84s. Two genuine REDs: resource-read and completed-run audit queries returned zero rows. The third was a test assertion that needed to decode nested JSON, not a production failure. |
| `--basetemp=/tmp/task5-focused-final -p no:cacheprovider backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py backend/tests/runtime/test_runner_gateway_skill_resources.py -q` | Exit 0: 30 passed, 1 warning, 25.49s. Positive/negative E2E and existing resource tests. |
| `--basetemp=/tmp/task5-gateway-terminal-red -p no:cacheprovider backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py -k gateway_terminal -q` | Exit 1: 3 failed, 20 deselected, 1 warning, 12.66s. Runner failed/timeout/cancelled completion persisted the Run terminal status but left a script lease `running`. |
| `--basetemp=/tmp/task5-e2e-final -p no:cacheprovider backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py -q` | Exit 0: 23 passed, 0 skipped, 1 warning, 27.71s. Before the long-resource-path regression was added. |
| `--basetemp=/tmp/task5-audit-path-red -p no:cacheprovider backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py -k long_resource -q` | Exit 1: 1 failed, 23 deselected, 1 warning, 11.93s. Rejected long relative resource path exceeded AuditRecorder's 200-character resource_name bound. The fix bounds display fields and metadata paths and adds the full original path's SHA-256. |

The warning in these commands is the existing Starlette `TestClient` import of deprecated `anyio.abc.BlockingPortal`. No warning was suppressed.

Fixture corrections, not counted as RED: removed a nonexistent `RunnerGatewayClient.close`; decoded resource content as nested JSON; used existing `tool.invoke.started/succeeded` audit names; replaced an invalid “unpublished” setup (an already-published historical version with an empty current pointer) with a genuinely unpublished Skill; checked `RunnerGatewayBusinessError.code`, because its public exception string is intentionally sanitized. The initial positive invocation with the teardown mistake reported 1 failed and 1 error; the clean rerun above confirmed the real production RED before code changes.

## Acceptance matrix

| Requirement | Evidence |
| --- | --- |
| Published immutable body presented to model | Positive integration test checks the actual first model messages, actual bound SkillVersion and real snapshot creation. |
| Reference and template materialized/read | Positive test checks actual model tool-return messages for both resources and bytes on the private Runner workspace; S3 package validation and Gateway HTTP routes remain real. |
| Declared script exposed/executed | Actual model schema contains the script; actual `sys.executable` subprocess transforms `7` to `8`; ToolInvocation lease and terminal record are persisted. |
| Built-in/MCP authorized invocation | Positive test checks actual tools supplied to the model, time/MCP return messages, both ToolInvocation records and both successful tool audit resource IDs. |
| Final answer and one run identity | Actual assistant message `FORECAST-VERIFIED: level=8; rule=add-one; source=water-mcp`, completed AgentRun, resource/script/tool/model/completion audit and failed resource audit all carry `run-1`. |
| Unauthorized/traversal resources | Five manifest rejection cases plus real Gateway unbound resource rejection and failed resource audit. |
| Undeclared script | Real Gateway rejects before creating a lease. |
| Tampered package/file | Two runtime tests fail before first model completion, leave no installed resource tree, and record failure. |
| Disabled/missing/unpublished/legacy binding | Four tests reject during real snapshot creation before model invocation. Existing Task 4 tests also cover reuse/availability toggling and explicit migration. |
| Script input schema/nonzero/oversized/timeout/cancel | Existing authoritative tests in `backend/tests/runtime/test_skill_scripts.py`: `test_execute_script_uses_json_protocol_and_validates_input_output`, `test_execute_script_rejects_traversal_missing_nonzero_and_oversized_output`, `test_execute_script_supports_timeout_and_cancellation`. New direct output-schema test rejects an output string where integer is required. |
| Approval/idempotency | Existing `test_script_tool_requires_valid_lease_and_reports_completion`, Gateway approval/resume tests and terminal concurrency tests are reused; positive script requires no approval, so its persisted lease is the applicable admission record. |
| All terminal leases/audit | Three Coordinator terminal cases plus three Gateway terminal callback cases; replay/cancel/recovery cannot create a second terminal script audit. |

## Broader verification

Initial diagnostic runs, started before the final production changes (not final-tree verification):

```powershell
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -v "I:\智能体平台\IntelligentAgentPlatform:/workspace" -w /workspace intelligent-agent-platform-api:local python -m pytest --basetemp=/tmp/task5-runtime-skills -p no:cacheprovider backend/tests/runtime backend/tests/skills backend/tests/test_agent_skill_bindings.py -q
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -v "I:\智能体平台\IntelligentAgentPlatform:/workspace" -w /workspace intelligent-agent-platform-api:local python -m pytest --basetemp=/tmp/task5-full-backend -p no:cacheprovider backend/tests -q
```

The runtime/skills/bindings diagnostic finished exit 1: 55 failed, 934 passed, 0 skipped, 2 warnings, 1340.69s. Of these, 47 cookie API failures were the explicit database-name safety guard. Three worker startup failures were initially suspected to involve PYTHONPATH, but stderr later disproved that hypothesis: the deadline probe's synthetic gateway_tools module lacked both newer Skill factory exports. Five remaining failures were reproduced separately: two import cycles, two leaked public DTO storage identities, and one stale summary field allowlist.

The initial full-backend diagnostic finished exit 1: 58 failed, 1680 passed, 101 skipped, 2 warnings, 2013.10s. It includes the same 55 failures plus an invalid published Skill fixture in `test_dispatcher`, a stale application route allowlist and the migration-head assertion. It began before final fixes and is not final-tree evidence. The invocation did not include `-ra`, so it did not print individual skip reasons; skipped tests are not counted as passing. At this checkpoint the controller still needed to repeat full backend and browser acceptance against a clean committed worktree; the completed evidence is recorded below.

All corrected-environment commands use this exact prefix. The working directory `/tmp` ensures that the specially named SQLite database is container-local and disposable; PYTHONPATH is inherited by spawned workers. No live service database is accessed.

```powershell
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPYCACHEPREFIX=/tmp/task5-pycache -e DATABASE_URL=sqlite:///iap_skill_control_test_20260908_a -e PYTHONPATH=/workspace/backend -v "I:\智能体平台\IntelligentAgentPlatform:/workspace" -w /tmp intelligent-agent-platform-api:local python -m pytest
```

| Arguments after corrected prefix | Exit / evidence |
| --- | --- |
| `--basetemp=/tmp/task5-env-green -p no:cacheprovider /workspace/backend/tests/skills/test_project_cookie_api.py -x -q` | Exit 0: 60 passed, 0 skipped, 2 warnings, 368.28s. |
| `--basetemp=/tmp/task5-e2e-current -p no:cacheprovider /workspace/backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py -q` | Exit 0: 24 passed, 0 skipped, 1 warning, 36.75s. Includes long resource path audit. Before the subsequent import/DTO fixes. |
| `--basetemp=/tmp/task5-contract-red -p no:cacheprovider /workspace/backend/tests/skills/test_project_publish.py::test_publish_commits_verified_snapshot_revision_pointer_and_one_audit /workspace/backend/tests/skills/test_project_reads.py::test_list_projects_only_explicit_summary_and_uses_literal_search /workspace/backend/tests/skills/test_project_reads.py::test_draft_and_version_dtos_include_files_without_storage_fields /workspace/backend/tests/skills/test_repository.py::test_models_register_in_fresh_process_for_any_import_order -q --tb=short` | Exit 1: 5 failed, 1 passed, 0 skipped, 0 warnings, 42.83s. Stable fresh-environment RED. |
| `--basetemp=/tmp/task5-import-green -p no:cacheprovider /workspace/backend/tests/skills/test_repository.py::test_models_register_in_fresh_process_for_any_import_order -q --tb=short` | Exit 0: 3 passed, 0 skipped, 0 warnings, 26.13s. |
| `--basetemp=/tmp/task5-dto-route-red -p no:cacheprovider /workspace/backend/tests/skills/test_project_availability.py::test_availability_api_updates_current_state_without_mutating_published_version -q --tb=short` | Exit 1: 1 failed, 0 skipped, 1 warning, 12.80s. New real HTTP assertion proves public storage-field leakage. |
| `--basetemp=/tmp/task5-dto-green -p no:cacheprovider /workspace/backend/tests/skills/test_project_publish.py::test_publish_commits_verified_snapshot_revision_pointer_and_one_audit /workspace/backend/tests/skills/test_project_reads.py::test_draft_and_version_dtos_include_files_without_storage_fields /workspace/backend/tests/skills/test_project_availability.py -q --tb=short` | Exit 0: 5 passed, 0 skipped, 1 warning, 23.77s. |
| `--basetemp=/tmp/task5-summary-green -p no:cacheprovider /workspace/backend/tests/skills/test_project_reads.py::test_list_projects_only_explicit_summary_and_uses_literal_search -q --tb=short` | Exit 0: 1 passed, 0 skipped, 0 warnings, 11.23s. |
| `--basetemp=/tmp/task5-project-contracts-final -p no:cacheprovider /workspace/backend/tests/skills/test_project_publish.py /workspace/backend/tests/skills/test_project_reads.py /workspace/backend/tests/skills/test_project_availability.py -q --tb=short -ra` | Exit 0: 64 passed, 0 skipped, 1 warning, 127.65s. Full three-file contracts, not just the newly added assertions. |
| `--basetemp=/tmp/task5-dispatch-routes-red -p no:cacheprovider /workspace/backend/tests/conversations/test_dispatcher.py::test_dispatcher_uses_bound_skill_snapshot_before_model_invocation /workspace/backend/tests/test_main.py::test_project_skill_routes_are_mounted_when_enabled_in_a_fresh_interpreter -q --tb=short` | Exit 1: 2 failed, 0 skipped, 2 warnings, 36.57s. Dispatcher fixture has no valid Skill frontmatter; main's subprocess helper additionally drops repo-root imports when parent cwd is /tmp. |
| `--basetemp=/tmp/task5-dispatch-green-routes-red -p no:cacheprovider /workspace/backend/tests/conversations/test_dispatcher.py::test_dispatcher_uses_bound_skill_snapshot_before_model_invocation /workspace/backend/tests/test_main.py::test_project_skill_routes_are_mounted_when_enabled_in_a_fresh_interpreter -q --tb=short` | Exit 1: 1 failed, 1 passed, 0 skipped, 2 warnings, 38.83s. Legal Skill fixture passes; after helper import-path repair the pure route failure shows the sole missing availability PATCH allowlist entry. |
| `--basetemp=/tmp/task5-dispatch-main-final -p no:cacheprovider /workspace/backend/tests/conversations/test_dispatcher.py /workspace/backend/tests/test_main.py -q --tb=short -ra` | Exit 0: 22 passed, 0 skipped, 2 warnings, 91.52s. |
| `--basetemp=/tmp/task5-worker-green -p no:cacheprovider /workspace/backend/tests/runtime/test_sandbox_runtime.py::test_real_worker_process_does_not_join_abandoned_team_deadline_work -q --tb=short` | Exit 0: 3 passed, 0 skipped, 0 warnings, 44.69s. Probe-only two-export adapter, no Team runtime changes. |
| `--basetemp=/tmp/task5-public-replay-red -p no:cacheprovider /workspace/backend/tests/skills/test_project_api.py::test_publish_route_returns_fixed_metadata_and_replay -q --tb=short` | Exit 1: 1 failed, 0 skipped, 1 warning, 12.71s. The old exact response allowlist still required the three internal storage fields removed under the established public DTO security contract. |
| `--basetemp=/tmp/task5-public-api-green -p no:cacheprovider /workspace/backend/tests/skills/test_project_api.py -q --tb=short -ra` | Exit 0: 38 passed, 0 skipped, 1 warning, 131.31s. Full API suite after deleting only those three allowlist entries. |

Root causes and minimal fixes: `skills.models -> db.base -> runtime.execution_snapshot -> skills.repository` attempted to import a partially initialized Skill module. Both execution-time repository imports are now local, consistent with existing runtime lazy imports, without touching user-owned `db/base.py`. Public `PublishedSkillInfo` and its projection now omit `object_key/archive_sha256/size_bytes`; runtime snapshot resolution still reads the complete SkillVersion ORM. Availability tests compare real stored identity before/after enablement and forbid those fields in HTTP responses. The summary's `enabled` field is an intentional Task 4 API contract; only its pre-existing exact-key allowlist was updated.

Dispatcher and main changes are test adapters, not relaxed production validation: the fixture now contains valid immutable Skill frontmatter, the child interpreter receives backend and repository-root import paths, and the intentional availability PATCH is included in the explicit route allowlist. A stderr-only probe confirmed `ModuleNotFoundError: No module named 'backend'` before the helper path fix.

Migration regression was independently verified without changing or moving the user's migration. The following archive contains only committed HEAD files, naturally excluding untracked `20260913_28`:

```powershell
New-Item -ItemType Directory -Path .superpowers/sdd/2026-09-13-single-agent-skill-runtime-plan/task5-clean-migration-tree
git archive --format=tar --output=.superpowers/sdd/2026-09-13-single-agent-skill-runtime-plan/task5-clean-migrations.tar HEAD backend/alembic backend/alembic.ini backend/tests/integration/test_postgres_migrations.py
tar -xf .superpowers/sdd/2026-09-13-single-agent-skill-runtime-plan/task5-clean-migrations.tar -C .superpowers/sdd/2026-09-13-single-agent-skill-runtime-plan/task5-clean-migration-tree
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPYCACHEPREFIX=/tmp/task5-pycache -e PYTHONPATH=/workspace/backend:/workspace -v "I:\智能体平台\IntelligentAgentPlatform:/workspace" -w /tmp intelligent-agent-platform-api:local python -m pytest --basetemp=/tmp/task5-migration-clean-red -p no:cacheprovider /workspace/.superpowers/sdd/2026-09-13-single-agent-skill-runtime-plan/task5-clean-migration-tree/backend/tests/integration/test_postgres_migrations.py::test_migration_graph_has_single_integration_head -q --tb=short
```

Exit 1: 1 failed, 0 skipped, 0 warnings, 10.65s; actual unique head `20260914_29` differs from obsolete `20260908_27`. After changing only the two old expected head values to `20260914_29` in the workspace test and archived test copy, the identical command with `--basetemp=/tmp/task5-migration-clean-green` passed: exit 0, 1 passed, 0 skipped, 0 warnings, 9.83s. This verifies graph shape only, not a live PostgreSQL upgrade. No migration source, dependency edge or merge revision changed. The dirty workspace still includes the user's independent head `20260913_28`; it remains unstaged and must not be attributed to Task 5.

The next broad verification command was recorded before launch. It finished exit 1: 4 failed, 1009 passed, 0 skipped, 2 warnings, 960.33s. Failures are exactly the three stale worker-probe imports and the publish/replay allowlist mismatch, fixed by the narrow cycles recorded above. Since its test tree changed while running, a new complete run is mandatory before final verification is claimed:

```powershell
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPYCACHEPREFIX=/tmp/task5-pycache -e DATABASE_URL=sqlite:///iap_skill_control_test_20260908_a -e PYTHONPATH=/workspace/backend -v "I:\智能体平台\IntelligentAgentPlatform:/workspace" -w /tmp intelligent-agent-platform-api:local python -m pytest --basetemp=/tmp/task5-runtime-skills-final -p no:cacheprovider /workspace/backend/tests/runtime /workspace/backend/tests/skills /workspace/backend/tests/test_agent_skill_bindings.py /workspace/backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py -q --tb=short -ra
```

Read-only worker stderr diagnostic (existing test behavior, no file edits or replacement production logic):

```powershell
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPYCACHEPREFIX=/tmp/task5-pycache -e DATABASE_URL=sqlite:///iap_skill_control_test_20260908_a -e PYTHONPATH=/workspace/backend:/workspace -v "I:\智能体平台\IntelligentAgentPlatform:/workspace" -w /tmp intelligent-agent-platform-api:local python -c "import pytest, subprocess, sys; import backend.tests.runtime.test_sandbox_runtime; children=[]; original=subprocess.Popen; subprocess.Popen=lambda *args,**kwargs: children.append(original(*args,**kwargs)) or children[-1]; code=pytest.main(['--basetemp=/tmp/task5-worker-diagnostic', '-p', 'no:cacheprovider', '/workspace/backend/tests/runtime/test_sandbox_runtime.py::test_real_worker_process_does_not_join_abandoned_team_deadline_work[planning]', '-q', '--tb=short']); print([(child.returncode, child.communicate()) for child in children]); sys.exit(code)"
```

Exit 1: 1 failed, 0 skipped, 2 warnings, 11.88s. Child stderr is `ImportError: cannot import name 'build_skill_resource_tools' from 'app.runtime.gateway_tools' (unknown location)`. The module is a test-only ModuleType replacement; it also lacks the Task 3 `build_skill_script_tools` export. Adding both existing no-tool stubs fixes the three worker tests. The diagnostic's two warnings are pytest assertion-rewrite warnings for already imported anyio and langsmith, caused by this temporary diagnostic wrapper, not production warnings.

This frozen-tree attempt was deliberately interrupted after independent review found two resource-audit P2 issues. The exec process exited 1 on Ctrl+C after passing progress beyond 42%, without a pytest summary. It is incomplete diagnostic evidence, not a final result:

```powershell
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPYCACHEPREFIX=/tmp/task5-pycache -e DATABASE_URL=sqlite:///iap_skill_control_test_20260908_a -e PYTHONPATH=/workspace/backend -v "I:\智能体平台\IntelligentAgentPlatform:/workspace" -w /tmp intelligent-agent-platform-api:local python -m pytest --basetemp=/tmp/task5-runtime-skills-verified -p no:cacheprovider /workspace/backend/tests/runtime /workspace/backend/tests/skills /workspace/backend/tests/test_agent_skill_bindings.py /workspace/backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py -q --tb=short -ra
```

## Independent review and resource race hardening

The requesting-code-review skill triggered a bounded independent read-only technical review. It identified two P2 regressions: holding the Run row lock across object-store reads blocks cancellation/deadline cleanup, and rejected NUL-containing paths cannot be persisted as PostgreSQL text/JSONB. Both were accepted after tracing the actual production paths and reproducing the boundaries. A follow-up also demonstrated stale ORM identity-map state during the final snapshot check.

The final implementation reads and verifies resources before acquiring the Run lock. Inside the existing transaction it then rechecks active state, calls the exact existing bearer/action verifier, refreshes and verifies snapshot identity, and atomically records success or failure before returning. A terminal Run, revoked token or changed snapshot never receives resource bytes and only gains a failed resource audit, without rewriting terminal state. `read_skill_file_audited` has one production caller, the resource router; it passes a mandatory reauthorization callback using the original Authorization header and existing token-service dependency. No token-verification rules were copied.

Audit display strings escape C0/C1 controls before truncation, and metadata stores the escaped path plus the SHA-256 of the original input. Raw controls are not copied to resource identity/name, RunEvent payload, audit metadata or error details. Snapshot `get` now uses `populate_existing=True`, consistent with `get_for_run` and the existing persisted-integrity contract. No lock is added around external I/O and no extra transaction is opened.

Commands below use the corrected-environment pytest prefix above:

| Arguments | Exit / evidence |
| --- | --- |
| `--basetemp=/tmp/task5-resource-review-red -p no:cacheprovider /workspace/backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py -k "storage_read or authority_after or resource_controls" -q --tb=short` | Exit 1: 7 failed, 24 deselected, 1 warning, 21.79s. Six real REDs: lock acquired before storage; cancelled, failed, completed, revoked and changed-snapshot cases incorrectly returned bytes. Seventh was a fixture URL-decoding problem, not business RED. |
| `--basetemp=/tmp/task5-resource-controls-red -p no:cacheprovider /workspace/backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py -k resource_controls -q --tb=short` | Exit 1: 1 failed, 30 deselected, 1 warning, 17.05s. After forwarding raw encoded paths in the HTTP test bridge, actual RunEvent ORM text contained NUL and violated the database-safe text check. |
| `--basetemp=/tmp/task5-resource-lock-green -p no:cacheprovider /workspace/backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py -k "storage_read or authority_after" -q --tb=short` | Exit 1: 1 failed, 5 passed, 25 deselected, 1 warning, 16.97s. All authority races were fixed; the remaining assertion incorrectly demanded exactly one lock, overlooking the existing audit sequence lock. |
| `--basetemp=/tmp/task5-resource-lock-green-confirmed -p no:cacheprovider /workspace/backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py -k "storage_read or authority_after" -q --tb=short` | Exit 0: 6 passed, 25 deselected, 1 warning, 15.93s. Assertion now requires no lock before storage and locking afterward, without constraining unrelated existing audit locks. |
| `--basetemp=/tmp/task5-resource-controls-green -p no:cacheprovider /workspace/backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py -k resource_controls -q --tb=short` | Exit 0: 1 passed, 30 deselected, 1 warning, 11.66s. Control-safe AuditEvent/RunEvent persisted and original path hash retained. |
| `--basetemp=/tmp/task5-resource-snapshot-cache-red -p no:cacheprovider /workspace/backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py -k "authority_after and snapshot" -q --tb=short` | Exit 1: 1 failed, 30 deselected, 1 warning, 14.88s. Holding the original ORM row while a separate transaction changes its digest made cached get incorrectly return bytes. |
| `--basetemp=/tmp/task5-resource-review-green -p no:cacheprovider /workspace/backend/tests/runtime/test_execution_snapshot.py /workspace/backend/tests/runtime/test_runner_gateway_skill_resources.py /workspace/backend/tests/runtime/test_script_terminal_lifecycle.py /workspace/backend/tests/runtime/test_run_lifecycle.py /workspace/backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py -q --tb=short -ra` | Exit 0: 111 passed, 0 skipped, 1 warning, 133.37s. Full snapshot/resource route/terminal/E2E combination, including all 31 E2E cases. |

The final independent read-only rereview reports both P2 issues and the cached-snapshot caveat resolved, with no remaining actionable concern in these fixes. The SQLite regression explicitly inspects real SQL locking intent and real ORM text values at the persistence boundary; it does not claim to execute PostgreSQL row-lock contention or a live PostgreSQL NUL insertion. Clean-tree PostgreSQL/deployment acceptance remains the controller's responsibility.

Standalone final E2E command:

```powershell
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPYCACHEPREFIX=/tmp/task5-pycache -e DATABASE_URL=sqlite:///iap_skill_control_test_20260908_a -e PYTHONPATH=/workspace/backend -v "I:\智能体平台\IntelligentAgentPlatform:/workspace" -w /tmp intelligent-agent-platform-api:local python -m pytest --basetemp=/tmp/task5-e2e-reviewed -p no:cacheprovider /workspace/backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py -q --tb=short -ra
```

Exit 0: 31 passed, 0 skipped, 1 warning, 37.11s. This includes both independent-review fixes and the retained-ORM snapshot race test.

Final post-review frozen-tree command, recorded before launch and completed without further production or test edits:

```powershell
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPYCACHEPREFIX=/tmp/task5-pycache -e DATABASE_URL=sqlite:///iap_skill_control_test_20260908_a -e PYTHONPATH=/workspace/backend -v "I:\智能体平台\IntelligentAgentPlatform:/workspace" -w /tmp intelligent-agent-platform-api:local python -m pytest --basetemp=/tmp/task5-runtime-skills-reviewed -p no:cacheprovider /workspace/backend/tests/runtime /workspace/backend/tests/skills /workspace/backend/tests/test_agent_skill_bindings.py /workspace/backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py -q --tb=short -ra
```

Exit 0: **1020 passed, 0 skipped, 2 warnings, 938.86s (15:38)**. This is evidence for implementation commit `760b83a7`, before formal fix round 1 below, covering every runtime and Skill test, Agent binding API tests, and all 31 then-existing E2E cases. It is not final-tree evidence for the availability fix. The two warnings are the existing Starlette BlockingPortal and Authlib jose deprecations; neither was suppressed. Earlier mixed-tree/incorrect-environment runs are diagnostic evidence only and do not replace this result.

An environment-only diagnostic is already complete:

```powershell
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -v "I:\智能体平台\IntelligentAgentPlatform:/workspace" -w /workspace intelligent-agent-platform-api:local python -m pytest --basetemp=/tmp/task5-env-probe -p no:cacheprovider backend/tests/skills/test_project_cookie_api.py -x -q
```

Exit 1: 1 failed, 1 warning, 11.05s. `project_support.py:180` deliberately refuses middleware assembly unless the configured database name equals `iap_skill_control_test_20260908_a`. This happens before the test-specific SQLite repository is used. The corrected container-local named SQLite rerun passed all 60 cookie tests as recorded above; no live database was used to bypass the guard.

Controller frontend evidence: in `frontend`, `& 'C:\Program Files\nodejs\npm.cmd' run build`, exit 0. The package command is `vue-tsc --noEmit && vite build`; no type errors, 6980 modules transformed, build 1m21s. One non-fatal Vite bundle-size warning: main `index-CKcJgKtZ.js`, 1571.75 kB / gzip 487.77 kB, above 500 kB.

Browser verification was pending at this checkpoint. Read-only checks found `/health` and `/api/health` return 200; `/chat` was usable with development identity. The deployed API then lacked the Task 4 availability and migration routes. Docker inspection confirmed API had only the `/data` named volume and Web had no mounts, so neither consumed current source automatically; old UI history was not accepted as evidence of the new runtime. The rebuilt-image acceptance is recorded below.

## Files and commit hygiene

Task 5-owned production changes: `gateway_tools.py`, `sandbox_runtime.py`, `runner_gateway_service.py`, `runner_gateway_router.py`, `script_lifecycle.py`, `run_lifecycle.py`, `execution_snapshot.py` under `backend/app/runtime`; the minimal public DTO safety fixes in `backend/app/skills/project_schemas.py` and `project_service.py`.

Additional owned regression-test adapters: `backend/tests/skills/test_project_reads.py`, `test_project_availability.py`, `test_project_api.py`, `backend/tests/conversations/test_dispatcher.py`, `backend/tests/test_main.py`, the missing no-tool exports in `backend/tests/runtime/worker_deadline_probe.py`, and the two expected migration head values in `backend/tests/integration/test_postgres_migrations.py`.

Task 5-owned artifacts: the new integration test, `docs/deployment/single-agent-skill-runtime.md`, the updated 2026-09-07 Skill productionization design, and this report.

One approved worktree-only adjustment is retained in user-owned `backend/tests/runtime/test_runner_gateway_skill_resources.py`: its newly added router test now creates an isolated real SQLite ConversationRepository to satisfy required audit persistence. The original user-added test does not exist in HEAD, so its fixture-only adaptation cannot be independently staged without also staging user content. Per controller direction the entire shared file will remain unstaged; equivalent production route/audit acceptance is committed in the new integration file. All other user edits and the untracked control migration `20260913_28` remain untouched.

Implementation commit: `760b83a7e624fa7e59ab825c916b50b61846de48` (`test: verify single-agent skill tool mcp linkage`), exit 0; exactly 20 files, 1002 insertions and 49 deletions. It contains the final production/test tree verified by **1020 passed** in the complete runtime/Skill/binding/E2E group and **31 passed** in the standalone E2E command. This report-only follow-up does not change that verified code or test tree.

The explicit 20-file Task 5 allowlist was verified before commit. `git diff --cached --name-only` confirmed no shared user test, control migration, user DB registration or unrelated change was included; `git diff --cached --check` exited 0 with no whitespace errors. The report was the only force-added path from the ignored task-report directory. `git show --format=oneline --name-only HEAD` confirmed the same 20-file commit scope after commit. The approved shared resource-router test adapter remains worktree-only and was not committed.

## Self-review

- No replacement core graph or manually executed model-tool loop; externally returned tool calls enter actual Deep Agents nodes and are read back in subsequent model history.
- Resource paths are manifest-scoped and schema-listed; no object key, host path, network credential or writable Artifact surface was added to the model.
- Run terminal and script terminal changes share existing row-lock ordering and conditional update. Durability occurs in the same transaction before revocation, and completion idempotency prevents duplicate terminal audit.
- Availability preserves historical versions; “unpublished” does not mean “not currently pointed to by latest.”
- No frontend production edits, database migration, knowledge/Embedding/GIS/Team protocol or Artifact changes are part of Task 5.
- The complete runtime/Skill/binding/E2E verification above predates fix round 1. At this checkpoint, clean-commit full-backend regression and a new visible browser answer remained controller-owned acceptance steps; neither old browser history nor the initial diagnostic full-backend run was claimed as final acceptance. The completed controller evidence is recorded below. Live PostgreSQL-only suites remain explicitly environment-gated in the final local run.

## Formal review fix round 1: current resource availability

The controller reopened Task 5 because a published Skill disabled after snapshot creation still returned resource bytes. Frozen `SnapshotSkill.enabled` is not current project availability. This fix is confined to `RunnerGatewayService.read_skill_file_audited`; no script execution or old runtime path is changed.

After external object I/O finishes, the existing Run row lock, active-run check, token reauthorization and refreshed snapshot/digest check still run. Before success audit or returning content, resources with a frozen `skill_id` now resolve the frozen `(skill_id, version_id)` through `SkillRepository.get_version(SkillScope(snapshot.unit_id, snapshot.project_id), ..., available_only=True)`. Missing or currently disabled identities produce HTTP 403 with existing `SkillUnavailableError.code` (`skill_unavailable`), persist `skill.resource.failed`, and return no bytes. There is no name lookup or latest-version fallback. Existing historical version availability rules remain owned by the repository.

The tests disable the real Skill using `SkillRepository.set_enabled` in an independent transaction, once before the request and once inside the storage read callback. Both assert frozen snapshot identity is unchanged and query the committed failure audit from a separate session. Failure audit identity remains the bounded public Skill name and relative resource path, with original path SHA-256; no object key, archive reference or storage identity is added to errors or audit metadata.

The independent fix review found my initial extra `schema_version == "5"` guard incorrectly targeted legacy Team snapshots, not single-agent snapshots. A real schema-5 Team actor with no project Skill IDs reproduced this regression. The unnecessary schema guard was removed; the compatibility case now verifies returned bytes and persisted success audit. The final independent read-only rereview reports this P2 resolved and no new actionable findings. No Team protocol or legacy resolution behavior is added or migrated.

All commands use the corrected-environment Docker prefix above with `/tmp` working directory. Exact new arguments and observed results:

| Arguments | Exit / evidence |
| --- | --- |
| `/workspace/backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py -k disabled_after_snapshot --basetemp=/tmp/task5-fix1-availability-red -p no:cacheprovider -q --tb=short -ra` | Exit 1: 2 failed, 31 deselected, 1 warning, 15.69s. Both before-read and during-read disable cases incorrectly returned bytes: `DID NOT RAISE RunnerGatewayBusinessError`. |
| `/workspace/backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py -k disabled_after_snapshot --basetemp=/tmp/task5-fix1-availability-green -p no:cacheprovider -q --tb=short -ra` | Exit 0: 2 passed, 31 deselected, 1 warning, 12.27s. Intermediate implementation before Team compatibility correction. |
| `/workspace/backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py -k preserves_legacy_team --basetemp=/tmp/task5-fix1-team-compat-red -p no:cacheprovider -q --tb=short -ra` | Exit 1: 1 failed, 33 deselected, 1 warning, 14.36s. The erroneous schema-5 check rejects the existing Team resource read. |
| `/workspace/backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py -k "disabled_after_snapshot or preserves_legacy_team" --basetemp=/tmp/task5-fix1-availability-compat-green -p no:cacheprovider -q --tb=short -ra` | Exit 0: 3 passed, 31 deselected, 1 warning, 13.21s. Final code tree, both availability denies and Team compatibility pass. |

The first eight-file combination (`--basetemp=/tmp/task5-fix1-availability-regression`) exited 0 with 130 passed, 1 warning, 147.23s, but started before the compatibility correction. It is intermediate evidence only. The final corrected-tree combination was launched with no subsequent production/test edits:

```powershell
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPYCACHEPREFIX=/tmp/task5-pycache -e DATABASE_URL=sqlite:///iap_skill_control_test_20260908_a -e PYTHONPATH=/workspace/backend -v "I:\智能体平台\IntelligentAgentPlatform:/workspace" -w /tmp intelligent-agent-platform-api:local python -m pytest /workspace/backend/tests/runtime/test_execution_snapshot.py /workspace/backend/tests/runtime/test_runner_gateway_skill_resources.py /workspace/backend/tests/runtime/test_script_terminal_lifecycle.py /workspace/backend/tests/runtime/test_run_lifecycle.py /workspace/backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py /workspace/backend/tests/skills/test_project_availability.py /workspace/backend/tests/runtime/test_agent_skill_bindings.py /workspace/backend/tests/test_agent_skill_bindings.py --basetemp=/tmp/task5-fix1-reviewed-regression -p no:cacheprovider -q --tb=short -ra
```

Final corrected-tree combination: **exit 0, 131 passed, 0 skipped, 1 warning, 147.50s (2:27)**. This includes all 34 current integration cases, snapshot/resource-route/terminal coverage and Task 4 availability/binding tests. The warning is the existing Starlette BlockingPortal deprecation; it was not suppressed. This round touches only the Gateway service, the owned integration test and this report; the shared user resource test, `db/base.py`, control migration 28 and other user changes remain unstaged and untouched. Full final-commit backend and new-image browser acceptance were controller-owned at this checkpoint and are resolved below.

## Controller final acceptance

The browser-only remediation is commit `3d56425` (`fix: add secure runner temporary filesystem`). It provides the non-root, read-only Worker with an exact, Inspector-enforced `/tmp` tmpfs and does not widen host mounts, environment, capabilities or networks. The remediation's focused controller rerun passed **41 tests**.

The rebuilt Launcher and Worker completed real browser Run `d0d15a3e-84e4-417d-b8ce-cc40ca66d243`. `/chat` displayed `TASK5-BROWSER-VERIFIED: Skill正文、reference、template、声明脚本、内置工具和MCP工具已在同一单智能体运行中贯通。`, both visible tool steps completed, the Run status was completed, and the browser console recorded zero errors. The same Run persisted successful calls for `skill.task5_browser.script.normalize`, `system.get_current_time` and `mcp.task5_mcp.water_level_f9afabd0`; reads of `SKILL.md`, `references/rules.txt`, `scripts/normalize.py` and `templates/result.json`; model, script, built-in tool, MCP tool, terminal and cleanup events/audits under one Run identity.

The isolated frontend command `vue-tsc --noEmit && vite build` exited `0`, transformed 6976 modules and built in 2m36s. Its only issue was the existing non-fatal Vite chunk-size warning.

The first complete-backend run found three transient real-worker readiness timeouts plus one stale workflow-runner Docker-inspection fixture. All three worker parameters passed in an immediate focused rerun. The fixture was missing the newly mandatory `HostConfig.Tmpfs` value; adding the real Launcher mapping made the focused group pass **4 passed**. A bare follow-up command intentionally demonstrated the existing dedicated-database guard (`47` Cookie API failures) and is not product evidence. The final command used the documented container-local SQLite name, `/tmp` working directory and backend PYTHONPATH:

```powershell
docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPYCACHEPREFIX=/tmp/task5-pycache-final -e DATABASE_URL=sqlite:///iap_skill_control_test_20260908_a -e PYTHONPATH=/workspace/backend -v "I:\\智能体平台\\IntelligentAgentPlatform\\.worktrees\\single-agent-skill-runtime-acceptance:/workspace" -w /tmp intelligent-agent-platform-api:local python -m pytest --basetemp=/tmp/task5-full-corrected-final -p no:cacheprovider /workspace/backend/tests -q -rs --tb=short
```

Final result: **exit 0, 1726 passed, 101 skipped, 2 warnings in 1683.80s (28:03)**. Skips are explicitly environment-gated PostgreSQL/MinIO/Windows tests; the two warnings are the pre-existing Starlette BlockingPortal and Authlib jose deprecations. No warning or failure was suppressed.
