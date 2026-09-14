# SDD ledger - plan: docs/superpowers/plans/2026-09-13-single-agent-skill-runtime-plan.md

Task 1: complete (commits 71b0d16, e75b6e2; focused runtime/resource tests 31 passed; review fixes applied, team attachment gap deferred by single-agent scope).

Task 2: complete (commits 21cb4db..04a3331; review round 4 clean; resource Gateway/materializer regression 41 passed, 1 Windows symlink-permission skip).

Task 3: initial implementation (commits ebf9553, 46d781f; focused regression 27 passed, 1 deprecation warning).

Task 3: fix round 1/5 (4 addressed, 4 open; new stderr/JSON regression; commit 3418b36; open: durable approval, terminal audit callback/lease validation, single-agent deadline cancellation, bounded separate stdout/stderr capture).

Task 3: fix round 2/5 (stderr/JSON regression addressed; commit 6c8e8e7; open: production approval resume bypasses ordinary ToolStore, terminal callbacks are mutable, single-agent cancellation propagation, deterministic post-exit stderr bound, unexpected executor errors falsely complete).

Task 3: fix round 3/5 (production approval resume, deterministic stderr bound, and unexpected executor failure addressed; commit a5f69d8; open: concurrent terminal callbacks, explicit user cancellation, deadline watcher reuse).

Task 3: fix round 4/5 (3 addressed, 0 open; commit 81a667d; scoped re-review clean).

Task 3: minor (deferred): coordinator timeout/failed terminal paths may leave a running script lease after token revocation; carry into Task 5 terminal-state/audit acceptance and final review.

Task 3: complete (commits ebf9553..81a667d, review clean; focused regression 194 passed, 3 existing real-worker probes deselected).

Task 4: initial implementation (commits 79e0c32, 9f1fff0; focused builder/harness/snapshot/sandbox checks passed; review open).

Task 4: review (4 Important open: authoritative publication state, immutable version binding, required snapshot on bound agents, real model/tool integration coverage; 1 Minor verification-evidence issue).

Task 4: fix round 1/5 (4 original Important addressed; commit c3aee9c; 4 new Important open: real legacy migration/copy, published script metadata preservation, target-Agent scope selection, team snapshot reuse; authoritative disabled/current-availability contract confirmed missing).

Task 4: minor (deferred): verification report lacks fully reproducible broad-suite output; migration route should use established management error translation.

Task 4: fix round 2/5 (legacy Agent JSON copy/migration defaults, immutable SkillVersion metadata/scripts, target-Agent project scope, Team name-only snapshot reuse, and authoritative disabled availability addressed; focused Docker regression 31 passed with 1 upstream TestClient deprecation warning; clean archived Task 4 view Alembic head 20260914_29).

Task 4: fix round 2 re-review (4 Important regressions addressed; 1 Important open: authoritative project Skill API cannot observe/change enabled; 2 Minor open: description normalization and migration roundtrip evidence).

Task 4: fix round 3 (authoritative scoped/audited availability API, enabled read models, runtime-only availability filtering, scalar-description normalization, API-to-snapshot regression, and PostgreSQL migration roundtrip evidence; focused verification 114 passed with upstream TestClient deprecation warnings; broad wrapper collections stopped without final summaries).

Task 5: complete (implementation and review commits through `21f9add`; browser tmpfs remediation commit `3d56425`; focused remediation regression 41 passed; real browser Run `d0d15a3e-84e4-417d-b8ce-cc40ca66d243` completed with Skill body, reference, template, declared script, built-in tool and MCP tool in one Run; frontend `vue-tsc --noEmit && vite build` exit 0; corrected-environment complete backend regression 1726 passed, 101 skipped, 2 upstream deprecation warnings, exit 0). One stale workflow-runner inspection fixture was corrected after the new required tmpfs control; its focused regression passed 4/4. The first broad run's three worker-start timing failures passed independently and in the final complete run.
