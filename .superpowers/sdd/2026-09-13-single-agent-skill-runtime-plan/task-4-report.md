# Task 4 Report: Unified Single-Agent Skill Context

Implementation commit: `79e0c32` (`fix: unify single-agent skill context assembly`)

## Implementation

- Added `build_skill_context`, a pure shared builder that returns system messages, normalized system/context text, bound Skill body, and the read-only resource index.
- `PlatformAgentHarness` receives the dispatcher-created execution snapshot and uses frozen Skill records before conversation history. Existing no-tool warnings and history ordering are unchanged.
- `SandboxRuntime` delegates Skill/context/resource-index construction to the shared builder while retaining Tool Gateway tools.
- Snapshot creation resolves every bound Skill before model invocation and raises stable `skill_unavailable` for missing, unpublished, and disabled bindings.
- `ThreadRunDispatcher` supplies the snapshot service for normal AgentInfo objects. Legacy injected doubles without `skill_names` remain compatible because they cannot express a Skill binding.

## TDD Evidence

RED commands and raw results:

```text
$env:PYTHONPATH='backend;backend/.pydeps'; <bundled-python> -m pytest --basetemp=backend/.task4-red-harness backend/tests/runtime/test_harness.py::test_non_sandbox_run_includes_the_bound_skill_snapshot_body_before_history -q
1 failed: expected bound Skill body in the non-sandbox system/context messages.

$env:PYTHONPATH='backend;backend/.pydeps'; <bundled-python> -m pytest --basetemp=backend/.task4-red-snapshot backend/tests/runtime/test_execution_snapshot.py::test_snapshot_rejects_unavailable_bound_skills_with_stable_code -q
3 failed: missing leaked SkillNotFoundError; unpublished and disabled bindings did not raise.

$env:PYTHONPATH='backend;backend/.pydeps'; <bundled-python> -m pytest --basetemp=backend/.task4-red-builder backend/tests/runtime/test_execution_snapshot.py::test_build_skill_context_returns_literal_messages_body_and_resource_index -q
1 failed: ImportError, build_skill_context was absent.
```

The pre-existing Sandbox external behavior already passed before the refactor, so it is characterization/GREEN evidence rather than a fabricated RED:

```text
docker run ... pytest ...::test_single_agent_skill_context_exposes_builtin_and_mcp_tools_through_gateway -q
1 passed, 1 PytestCacheWarning
```

GREEN commands and raw results:

```text
builder + unavailable snapshot tests: 4 passed in 2.02s
non-sandbox Skill/history/no-tool focused tests: 3 passed in 3.36s
sandbox Skill/context/resource-index/Gateway tests (Docker): 3 passed, 1 PytestCacheWarning in 10.49s
sandbox independent Skill/Gateway regression (Docker): 1 passed, 1 PytestCacheWarning in 10.53s
dispatcher regression: 13 passed in 20.27s
```

## Changed Files

- `backend/app/runtime/execution_snapshot.py`
- `backend/app/runtime/harness.py`
- `backend/app/conversations/dispatcher.py`
- `backend/app/runtime/sandbox_runtime.py`
- `backend/tests/runtime/test_execution_snapshot.py`
- `backend/tests/runtime/test_harness.py`
- `backend/tests/runtime/test_single_agent_skill_context.py`

## Self-Review And Concerns

- The builder accepts only frozen `SnapshotSkill` records; Skill text does not alter the Gateway tool allowlist.
- Legacy `SkillInfo` objects lack a `published` field. The resolver treats that absence as published for backward compatibility, while explicit `published=False`, missing, or `enabled=False` fails closed.
- `backend/tests/runtime/test_sandbox_runtime.py` contains pre-existing user modifications. The independent sandbox test above replaces the Task 4 test hunk I initially added there, so no user sandbox changes are staged.
- The broad Docker regression command twice returned no exit summary from the execution harness, and a local resource/Gateway collection lacked `botocore`. These are not recorded as passing; focused Docker sandbox verification and local dispatcher/snapshot/harness verification are recorded above.

## Fix Round 1 Controller Decision

The controller authorized the minimum Agent/Skill binding expansion required for immutable published Skill execution. Agent configuration is already JSON, so this round will persist ordered `{skill_id, version_id}` bindings in that config without an Alembic migration. Agent create/update will resolve compatible `skill_names` immediately to the authoritative `Skill.published_version_id`; runtime snapshots will read the exact immutable `SkillVersion` through the scoped Skill repository and fail closed for legacy name-only agents. The expanded scope includes Agent schemas/service/router/store serialization, authoritative Skill repository reads, runtime snapshot/dispatcher enforcement, and focused tests. `backend/tests/runtime/test_sandbox_runtime.py` remains user-owned and untouched.

## Fix Round 1 Implementation

- Added frozen `SkillBinding` records to Agent JSON configuration. Create and update resolve ordered `skill_names` through the scoped `Skill.published_version_id`, overwrite client-provided bindings, and return those authoritative bindings.
- Added `SkillRepository.get_published_by_name` and `AgentService.migrate_legacy_skill_bindings`, exposed at `POST /{agent_id}/skill-bindings/migrate`. Migration uses the existing Agent mutation-scope check and persists only bindings resolved in that scope.
- Snapshot creation now reads only the exact scoped `SkillVersion` identified by each binding. Name-only legacy Agent configurations and missing versions fail with `skill_unavailable`; persisted snapshots revalidate their immutable version package/content identity before reuse.
- Bound or legacy named Agents cannot invoke a model without an execution snapshot service. The thread dispatcher supplies that service independently of its best-effort preview lookup.
- Replaced the scripted Sandbox graph test with the installed Deep Agents, GatewayChatModel, LangGraph, and GatewayStructuredTool path. The model gateway responses are the only fake external boundary.

### Fix Round 1 TDD and Verification

RED:

```text
docker run ... pytest --basetemp=/tmp/task4-r1-red -p no:cacheprovider tests/test_agent_skill_bindings.py -q
2 failed in 5.98s
- create raised `AgentValidationError: Unknown skills: forecast` from mutable SkillService
- migrate_legacy_skill_bindings was absent

docker run ... pytest --basetemp=/tmp/task4-r1-real-adapter -p no:cacheprovider tests/runtime/test_single_agent_skill_context.py -q
1 failed in 9.82s
- real Deep Agents supplied framework tools in addition to the two authorized platform tools; the test assertion was corrected to require both platform schemas without rejecting framework tools
```

GREEN:

```text
tests/test_agent_skill_bindings.py: 3 passed in 4.90s
tests/runtime/test_agent_skill_bindings.py tests/runtime/test_execution_snapshot.py: 24 passed in 7.51s
tests/conversations/test_dispatcher.py::test_dispatcher_uses_bound_skill_snapshot_before_model_invocation: 1 passed in 7.32s
tests/runtime/test_single_agent_skill_context.py: 1 passed in 8.24s
tests/test_agents.py::test_rejects_unknown_tool_bindings[update]: 1 passed, 1 TestClient deprecation warning in 6.75s
```

The local bundled Python collection remains blocked by a missing `botocore`; all repository/SkillVersion tests above use the project API Docker image. Two broad Docker invocations (`tests/test_agents.py` and the combined Task 4 list) stopped emitting a final pytest summary in the execution harness. They are intentionally not recorded as passing; isolated covering tests have complete output.

## Fix Round 2

This round closes the post-round-one review findings without changing Team snapshot protocols. Legacy Agent JSON is parsed through `AgentConfig` defaults before migration or copy, existing project Agents resolve their bindings in their persisted project scope, and common Agents reject project-scoped Skill bindings. Published `SkillVersion.content` is parsed with the existing `parse_skill_markdown` parser; its exact frontmatter name and description must match the immutable version, and its `metadata` is retained in the frozen snapshot. The existing script declaration validator runs against that frozen metadata before persistence.

`skills.enabled` is authoritative current availability. The self-contained `20260914_29` Alembic revision depends on tracked head `20260908_27`, defaults pre-existing rows to enabled, and removes the server default after migration. The untracked control-domain `20260913_28` is an independent sibling and must be merged later by its owner; this Task 4 commit neither depends on nor includes it. Both published-name resolution and exact-version resolution filter unavailable Skills, so Agent create/update/migrate plus fresh and reused single-Agent snapshots fail closed. Reused Team snapshots are intentionally exempt because Team definitions capture name-only protocol Skills, not live SkillVersion identities. The migration route now uses the standard management error/audit wrapper.

### Fix Round 2 TDD And Verification

RED commands and raw results:

```text
docker run --rm -v "${PWD}:/app" -w /app/backend intelligent-agent-platform-api:local python -m pytest --basetemp=/tmp/task4-r2-red-legacy-scope -p no:cacheprovider tests/test_agent_skill_bindings.py -q
...FF
2 failed, 3 passed in 5.91s
- raw persisted legacy JSON raised KeyError: 'skill_bindings' during migration
- a unit administrator in project B resolved a project-A Agent's Skill name to project B

docker run --rm -v "${PWD}:/app" -w /app/backend intelligent-agent-platform-api:local python -m pytest --basetemp=/tmp/task4-r2-red-metadata -p no:cacheprovider tests/runtime/test_agent_skill_bindings.py::test_bound_snapshot_preserves_declared_scripts_from_immutable_version -q
1 failed in 5.42s
- snapshot metadata omitted frontmatter metadata.scripts (KeyError: 'scripts')

docker run --rm -v "${PWD}:/app" -w /app/backend intelligent-agent-platform-api:local python -m pytest --basetemp=/tmp/task4-r2-red-team-reuse -p no:cacheprovider tests/runtime/test_execution_snapshot.py::test_team_snapshot_can_be_reused_when_captured_skills_are_name_only -q
1 failed in 4.65s
- second Team snapshot create raised skill_unavailable for name-only captured Skill records

docker run --rm -v "${PWD}:/app" -w /app/backend intelligent-agent-platform-api:local python -m pytest --basetemp=/tmp/task4-r2-red-availability -p no:cacheprovider tests/test_agent_skill_bindings.py::test_agent_skill_binding_resolution_rejects_disabled_published_skill tests/runtime/test_agent_skill_bindings.py::test_snapshot_rejects_disabled_bound_skill_on_fresh_and_reused_runs -q
4 failed in 7.33s
- disabled authoritative Skills were accepted at create/update and fresh snapshot resolution; migration still exposed the legacy KeyError
```

GREEN commands and raw results:

```text
docker run --rm -v "${PWD}:/app" -w /app/backend intelligent-agent-platform-api:local python -m pytest --basetemp=/tmp/task4-r2-green-bindings -p no:cacheprovider tests/test_agent_skill_bindings.py -q
8 passed in 5.60s

docker run --rm -v "${PWD}:/app" -w /app/backend intelligent-agent-platform-api:local python -m pytest --basetemp=/tmp/task4-r2-green-runtime-bindings -p no:cacheprovider tests/runtime/test_agent_skill_bindings.py -q
4 passed in 5.32s

docker run --rm -v "${PWD}:/app" -w /app/backend intelligent-agent-platform-api:local python -m pytest --basetemp=/tmp/task4-r2-green-snapshots-final -p no:cacheprovider tests/runtime/test_execution_snapshot.py -q
23 passed in 7.21s

docker run --rm -v "${PWD}:/app" -w /app/backend intelligent-agent-platform-api:local python -m pytest --basetemp=/tmp/task4-r2-green-skill-resources -p no:cacheprovider tests/runtime/test_skill_resources.py tests/runtime/test_single_agent_skill_context.py -q
16 passed in 9.87s

docker run --rm -v "${PWD}:/app" -w /app/backend intelligent-agent-platform-api:local python -m pytest --basetemp=/tmp/task4-r2-green-all-targeted -p no:cacheprovider tests/test_agent_skill_bindings.py tests/runtime/test_agent_skill_bindings.py tests/runtime/test_skill_scripts.py tests/runtime/test_runner_gateway_skill_resources.py -q
31 passed, 1 warning in 12.08s

docker run --rm -v "${PWD}/backend/.tmp-task4-alembic-self-contained:/app" -w /app/backend intelligent-agent-platform-api:local alembic heads
20260914_29 (head)
```

The one warning is Starlette's upstream `TestClient` deprecation for AnyIO's `BlockingPortal` alias. It is emitted by the existing resource test dependency path and does not indicate a Task 4 behavior failure.

## Fix Round 3

This round adds the authoritative project Skill availability contract that was
missing from the round-two review. `PATCH /api/project-skills/{skill_id}/availability`
requires `skill.manage`, resolves the target in the authorized scope, locks the
Skill row, advances the current draft revision with the existing conditional
update, and records `skill.availability.update` with the management request and
trace identifiers. The endpoint returns the current `SkillSummary`; create,
list, publish, version-list, and version-detail contracts now expose `enabled`.
Changing availability never writes a `SkillVersion`.

Management version reads intentionally remain observable when a Skill is
disabled. Runtime resolution and stored single-Agent snapshot reuse explicitly
request `available_only=True`, so both fail closed before a model invocation.
The immutable-version frontmatter comparison now normalizes YAML descriptions
with `str`, matching package persistence.

### Fix Round 3 TDD And Verification

RED:

```text
tests/skills/test_project_availability.py: 3 failed
- publish response omitted enabled
- authorized availability PATCH route was absent (404)

tests/runtime/test_agent_skill_bindings.py::test_snapshot_normalizes_scalar_frontmatter_description_from_bound_version: 1 failed
- YAML scalar description 123 was compared to persisted string "123" without normalization
```

GREEN:

```text
docker run --rm -v "${PWD}:/app" -w /app/backend intelligent-agent-platform-api:local python -m pytest --basetemp=/tmp/task4-r3-green-availability-rerun -p no:cacheprovider tests/skills/test_project_availability.py tests/skills/test_project_api.py::test_publish_route_returns_fixed_metadata_and_replay tests/runtime/test_agent_skill_bindings.py::test_snapshot_normalizes_scalar_frontmatter_description_from_bound_version -q
5 passed, 1 upstream TestClient deprecation warning in 14.44s

docker run --rm -v "${PWD}:/app" -w /app/backend intelligent-agent-platform-api:local python -m pytest --basetemp=/tmp/task4-r3-green-api-snapshot -p no:cacheprovider tests/runtime/test_agent_skill_bindings.py::test_availability_api_blocks_fresh_and_reused_snapshots_then_restores_bound_version -q
1 passed, 1 upstream TestClient deprecation warning in 7.79s

docker run --rm -v "${PWD}:/app" -w /app/backend intelligent-agent-platform-api:local python -m pytest --basetemp=/tmp/task4-r3-bindings-snapshots -p no:cacheprovider tests/test_agent_skill_bindings.py tests/runtime/test_agent_skill_bindings.py tests/runtime/test_execution_snapshot.py -q
37 passed, 1 upstream TestClient deprecation warning in 18.17s

docker run --rm -v "${PWD}:/app" -w /app/backend intelligent-agent-platform-api:local python -m pytest --basetemp=/tmp/task4-r3-runtime-boundaries -p no:cacheprovider tests/runtime/test_harness.py tests/runtime/test_single_agent_skill_context.py tests/runtime/test_tool_gateway_adapter.py tests/runtime/test_skill_scripts.py tests/runtime/test_runner_gateway_skill_resources.py tests/runtime/test_skill_resources.py -q
71 passed, 1 upstream TestClient deprecation warning in 45.65s
```

Two broad collections (`tests/skills` and a combined project-contract group)
were stopped after they continued producing progress without a final wrapper
summary. They are not recorded as passing or failing; the focused project API,
availability, repository-path, binding, snapshot, harness, adapter, script, and
resource checks above have complete exit summaries.

### PostgreSQL Migration Roundtrip

The healthy compose PostgreSQL service was used with a dedicated database,
`iap_task4_availability_r3`, which was removed after verification. The clean
archived Task 4 migration view was upgraded to `20260908_27`; a legacy Skill
row was inserted; then the archive upgraded to `20260914_29`. The database
query returned `true:NULL`, proving both legacy backfill and removal of the
temporary server default. An ORM insert using the current workspace model then
returned `True`, proving its client-side `default=True` remains effective after
the server default is removed. The archived migration view downgraded cleanly
to `20260908_27`; a final query returned `20260908_27:false`, confirming the
revision and absence of the `enabled` column. The dedicated database was then
dropped.
