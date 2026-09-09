# Skill Control Plane Verification

Status: COMPLETE for the isolated Skill control-plane API phase. Final source `aa06783` passed the full backend regression. This is not a production release or production integration approval.

## Scope

- Approved plan: [Skill control plane](2026-09-08-skill-control-plane.md).
- Branch: `codex/skill-control-plane`; source baseline `51c1aad`.
- Independent `/api/project-skills` test applications only. No production router mounting, old Skill API replacement, frontend/runtime switch, business-data migration, push, or deployment.
- Worktree: `.worktrees/skill-version-foundation`. The existing directory name is retained to reuse its Python environment.
- Dedicated service database: `iap_skill_control_test_20260908_a`.
- Dedicated migration database: `iap_skill_control_migration_20260908_a`.
- MinIO tests use newly generated UUID buckets and clean up only their own resources. PostgreSQL immutable-version fixtures can remain in the dedicated test database.

## Task Evidence

| Task | Commits | Evidence | Review |
| --- | --- | --- | --- |
| 1. Project authorization and additive permissions | `1fb5dd0..0d90e97` | Focused 62 passed, 1 Windows POSIX-only skip; real migration 3 passed, no skips; controller legacy Skill regression 29 passed | Approved; historical RED qualification below |
| 2. Scoped reads and Cookie API | `0d90e97..9593ad4` | Post-fix covering suite 86 passed, no skips, 146.28 s | Approved after selected-project ordering and shared summary-projection fixes |
| 3. Verified drafts and atomic audit | `9593ad4..9ac21b1` | Focused 66 passed, no skips, 148.77 s; real PostgreSQL/MinIO 13 passed, no skips, 6.84 s; controller post-commit API/Cookie 25 passed, 45.66 s | Functional spec and quality approved; qualifications below |
| 4. Atomic import | `9ac21b1..4078fd9` | Focused 83 passed; real PostgreSQL/MinIO 20 passed; post-fix API/Cookie/import 74 passed; all without skips | Approved after explicit-empty manifest fix |
| 5. Verified idempotent publication | `4078fd9..e19d6a4` | Final focused 160 passed, no skips, 265.97 s; real PostgreSQL/MinIO and version suite 42 passed, no skips, 14.38 s | Spec and quality approved |

Task 1 authorization RED rejected improper grants and asserted own-owner sets. The directory and migration RED demonstrated missing project-admin management grants. Migration tests use frozen pre-upgrade records, check only the intended additive grant delta, preserve role states/custom grants, and validate new-unit bootstrap. The actual migration is `20260908_27_project_skill_permissions.py`; downgrade intentionally retains grants.

Task 2 behavioral RED demonstrated owner leakage into results/count and automatic selection of a different project after the persisted selection became invalid. The shared authorization guard now checks identity consistency, persisted selected project, active project, then capability. A second real-Cookie RED demonstrated an incorrect permission error before the selected-project guard; fix `9593ad4` corrected that order. Synthetic contexts without a persisted session remain test-supported.

Task 3 behavioral RED demonstrated missing attachment bytes after actual package reconstruction and persisted draft readback. Actual Cookie middleware RED demonstrated an accepted write without the required CSRF header. GREEN checks verified immutable old objects, attachment retention, full snapshot comparison, storage-corruption rejection, named uniqueness errors, and unknown database/audit error propagation. Real PostgreSQL tests verified concurrent create/save results, rollback, and no checked-out connection or row lock during object I/O. Real MinIO tests exercised the default factory and corruption/missing-object failures.

Task 4 initial behavioral RED showed one committed Skill after the second object upload failed. The atomic implementation passed manifest, mixed create/update rollback, published-pointer preservation, attachment-preserving rename, and skip tests. PostgreSQL race cases observed actual `pg_blocking_pids`, not guessed delays. Final initial gates were 83 passed in 142.18 s and 20 real-service tests passed in 9.29 s. Review then found explicit multipart `manifest=""` became `None` through FastAPI normalization and incorrectly enabled default creation. Actual HTTP RED found Skill/Draft/Audit counts `(1,1,1)` instead of zero. The raw FormData guard in `4078fd9` fixed this; 74 covering tests passed in 130.32 s and scoped re-review approved. Controller independently reran empty-versus-omitted cases: 2 passed, 26 deselected, 1 existing warning, 6.13 s. Empty input returns 422, no rows/objects, and closed uploads; omitted input still creates.

Task 5 first established an actual verified publication. Its subsequent replay RED produced two revision conflicts after that real publication, with and without a later draft and with storage unavailable. Additional concurrent-commit RED cases demonstrated incorrect conflict/503 handling. The replay-first implementation preserves the historical canonical request digest, checks permission and scope before replay, avoids storage initialization for committed results, and records exactly one successful audit. Real PostgreSQL tests exercised same-key/different-key/different-actor races, draft changes during verification, concurrent committed replay during failed reads, and commit/audit rollback. The real Cookie matrix covers all nine methods plus CSRF/Origin protection and revoked-permission replay denial.

An initial Task 5 integration run had 13 passing business assertions but 12 teardown errors when fixture cleanup attempted to delete immutable versions. It is explicitly not counted as GREEN. The fixture now follows existing repository integration policy: retain publication-owning UUID scopes in the dedicated test database without disabling triggers, and clean unversioned fixture scopes. Owned MinIO buckets are still removed. The corrected complete integration suite passed 42 tests, including its teardown.

## Controller Commands

Commands run from the worktree use `.venv/Scripts/python.exe` with `PYTHONPATH=backend`. A plan-local PowerShell helper injects test configuration only into the child process and restores the caller environment in `finally`. `ordinary` clears external `TEST_*`; `service` supplies the dedicated service database and MinIO; `migration` uses only the dedicated migration database. Credentials are read into process memory and are not written to this report or Git.

The controller independently verified both successful and failing-child environment restoration. Ordinary child processes had no `TEST_*` keys; service processes had dedicated configuration; prior sentinel values were restored and originally absent keys remained absent. A deliberately failing child exited 17 and still restored `TEST_*`, `PYTHONPATH`, and `DATABASE_URL`. `Remove-Item Env:` is necessary here because the earlier .NET null assignment left empty environment keys.

Independent Task 3 post-commit check:

```powershell
& ./.superpowers/sdd/2026-09-08-skill-control-plane/run-validation.ps1 -Environment ordinary -PythonArguments @('-m','pytest','backend/tests/skills/test_project_api.py','backend/tests/skills/test_project_cookie_api.py','-q','-p','no:cacheprovider','--tb=short')
```

Result: 25 passed, 2 warnings, no skips, 45.66 s, exit 0. `pip check` also returned `No broken requirements found` alongside the full backend gate.

## Review Qualifications

- Task 1 added one missing-permission migration rollback test after the original GREEN. A controlled fail-open mutation was caught, the source hash was restored, and migration/graph tests passed. This verifies the test but does not retroactively establish test-first chronology.
- Task 3 initially began implementation after missing-interface/HTTP405 failures. Those were explicitly excluded as behavioral RED evidence. The unverified attachment replacement was reduced to manifest-only reconstruction; two actual missing-attachment failures were observed before the preservation implementation. The review accepted the functional result with the historical process deviation disclosed, not erased.
- Existing Starlette/AnyIO and Authlib deprecation warnings remain. They are not suppressed and do not establish a dependency regression.
- Authorization grants are a per-request snapshot; revocation is effective on the next authenticated request, as specified. Revalidation during an operation checks the existing context and current project/session selection, not linearizable in-flight grant revocation during external storage I/O.
- Escaped lone-surrogate JSON can trigger HTTP500 in the existing default validation-error response before any service mutation. The independent reproduction found zero new Skill rows and zero stored objects. FastAPI's default `request_validation_exception_handler` sends the invalid `input` through `jsonable_encoder`; Starlette `JSONResponse.render` uses `ensure_ascii=False` followed by UTF-8 encoding, which rejects the surrogate. This stage intentionally leaves shared exception handling and dependencies unchanged. A later shared-framework hardening change must add a durable regression test and preserve the intended validation-response contract.

The surrogate reproduction sends exact request bytes `b'{"content":"\\ud800"}'`, with JSON content type and an authenticated manage context, to POST `/api/project-skills` in the isolated test app. With `TestClient(..., raise_server_exceptions=False)`, the result is 500; a fresh database session and MemoryS3 inspection show no changes. This was a diagnostic assertion of the current limitation, not a passing 422 contract test. Ordinary malformed-input tests must not be described as universal 422 coverage.

## Final Gates

- Whole-branch review: completed on `51c1aad..e19d6a4`; P1 migration-target protection and P3 migration-description findings fixed in `aa06783`. Scoped re-review approved both with no new breakage; no unresolved review blocker.
- Controller focused Skill/identity/collaboration regression at `e19d6a4`: 512 passed, 1 POSIX-only skip, 2 warnings, 516.31 s, exit 0.
- Controller real PostgreSQL/MinIO/version/default-storage/upgrade-head gate: 44 passed, no skips, 1 warning, 14.66 s, exit 0. Separate dedicated permission-migration gate: 3 passed, no skips, 1 warning, 13.60 s, exit 0.
- First full backend regression at `e19d6a4`: 1530 passed, 3 failed, 100 skipped, 2 warnings, 1219.93 s, exit 1. This is NOT GREEN.
- Final full backend regression at `aa06783`: 1556 passed, 100 skipped, 2 warnings, 1211.18 s, exit 0 (controller session 66856). The three unchanged worker cases passed; no runtime source, test timeout, assertion, or plugin was changed.
- Controller post-fix guard check at `aa06783`: 23 passed, 3 expected integration skips, 1 warning, 0.08 s, exit 0. Dedicated permission migration: 3 passed, no skips, 1 warning, 15.57 s, exit 0.
- Controller post-fetch/merge real PostgreSQL/MinIO/version/default-storage/upgrade-head confirmation at unchanged `aa06783`: 44 passed, no skips, 1 warning, 16.77 s, exit 0.
- Dependency check: `No broken requirements found`, exit 0.
- Final protected-path scope diff from `316f511` is empty for `backend/app/main.py`, old `skills/router.py` and `skills/service.py`, frontend, agents, collaboration, and runtime. This stage did not mount the new production router or change those consumers.
- Final secure fetch succeeded; `git merge --no-edit origin/main` reported `Already up to date`. Remote main remained `242c91d5d96c64e0dd966e1536a4be2431de1b4a`; local main remained `51c1aad211d96449f439a44addd18f3c5316aa6f`. Backend source stayed at `aa06783262653c30244d3491b3d90cd65294519c` for final verification and the documentation-only handoff commit.

The three initial full-suite failures were the unchanged `test_real_worker_process_does_not_join_abandoned_team_deadline_work` planning/member/synthesis cases at `backend/tests/runtime/test_sandbox_runtime.py:3030`. Their child did not produce its ready marker within the existing 10-second startup wait. This occurs before the separate 2.5-second post-entry exit assertion. The file and runtime implementation have no change from baseline. Isolated exact-case rerun: 3 passed in 25.53 s; entire unchanged sandbox test file: 63 passed in 32.00 s. No assertion or time limit was changed. These results establish non-isolated/transient startup behavior, not a proven detailed scheduling cause or a full-suite success.

Both full runs' 100 skips comprise 97 external-integration configuration skips and 3 Windows POSIX/symlink limitations. The 47 explicitly required real-integration cases were executed separately as documented above, including final-source confirmation; other configuration-dependent integration cases were not validated by the ordinary run. Do not sum the overlapping suite counts as unique tests.

The final safety fix first demonstrated 22 failures against the actual unguarded fixture using a recording fake engine, without contacting any wrong target. Its guard requires PostgreSQL, the exact dedicated migration database, and a query-free URL before engine creation; it additionally checks `current_database()` before resetting the dedicated schema. A malformed-port regression verifies generic, credential-safe rejection. Final implementer gates: 23 guard tests passed (3 real migration tests intentionally skipped in ordinary mode), then 3 real migration tests passed with no skips in 14.02 s. The migration fixture and regression tests plus README were the only files changed by this fix. It neither modified runtime code/time limits nor disabled immutable database triggers.

## Final Command Record

The following are the exact historical controller commands, executed sequentially in the worktree. The plan-local helper is temporary orchestration, not a committed project command. After this report is committed, only this plan's ignored scratch directory is eligible for cleanup; the named branch, worktree, virtual environment, and dedicated databases are retained. To repeat the pytest selections after cleanup, use `.venv/Scripts/python.exe` with `PYTHONPATH=backend`, inject credentials through process environment, and apply the three environment modes documented above. Never point the migration selection at a business database or combine general downgrade tests with the service database.

```powershell
& ./.superpowers/sdd/2026-09-08-skill-control-plane/run-validation.ps1 -Environment ordinary -PythonArguments @('-m','pytest','backend/tests','-q','-ra','-p','no:cacheprovider','--tb=short')
& ./.superpowers/sdd/2026-09-08-skill-control-plane/run-validation.ps1 -Environment ordinary -PythonArguments @('-m','pytest','backend/tests/skills/test_project_migration_database_guard.py','backend/tests/integration/test_skill_project_permissions_postgres.py','-q','-ra','-p','no:cacheprovider','--tb=short')
& ./.superpowers/sdd/2026-09-08-skill-control-plane/run-validation.ps1 -Environment migration -PythonArguments @('-m','pytest','backend/tests/integration/test_skill_project_permissions_postgres.py','-q','-ra','-p','no:cacheprovider','--tb=short')
git -c http.sslVerify=true -c http.sslBackend=openssl -c http.proxy= -c http.version=HTTP/1.1 -c http.sslVersion=tlsv1.2 -c http.lowSpeedLimit=1 -c http.lowSpeedTime=15 fetch origin
git merge --no-edit origin/main
& ./.superpowers/sdd/2026-09-08-skill-control-plane/run-validation.ps1 -Environment service -PythonArguments @('-m','pytest','backend/tests/integration/test_skill_control_plane_postgres.py','backend/tests/integration/test_skill_control_plane_minio.py','backend/tests/integration/test_skill_versions_postgres.py','backend/tests/integration/test_skill_package_minio.py','backend/tests/integration/test_postgres_migrations.py::test_upgrade_head_creates_conversation_tables','-q','-ra','-p','no:cacheprovider','--tb=short')
& .venv/Scripts/python.exe -m pip check
git diff --name-only 316f511 HEAD -- backend/app/main.py backend/app/skills/router.py backend/app/skills/service.py frontend backend/app/agents backend/app/collaboration backend/app/runtime
git diff --check
```

No frontend/browser tests have been claimed or substituted for the new API checks. No main integration, push, or deployment is authorized by completion of this stage; integration remains a fresh user decision. The final review's single consolidated fix wave is complete, both findings are addressed, and the qualifications above remain visible rather than being represented as fixed.
