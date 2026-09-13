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
