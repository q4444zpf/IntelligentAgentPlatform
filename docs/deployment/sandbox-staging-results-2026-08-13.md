# Sandbox Staging Acceptance Results - 2026-08-13

## Scope

This acceptance run validates the platform lifecycle around isolated Workflow Runner containers. It covers timeout, memory exhaustion, Launcher outage, terminal database state, Run events, audit events, artifact absence on failure, container cleanup, and cleanup recovery after Launcher restart.

It does not claim that the production worker already executes a complete LangGraph/DeepAgents business snapshot or uploads real business artifacts. The acceptance image used controlled staging-only workload modes under the same container policy.

## Environment Safety

- Launcher token was generated in the staging PowerShell process only.
- No token was written to source files or Git configuration.
- Launcher was removed after the tests.
- Workflow Runner was restored to `IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED=false`.
- No `iap-run-*` container remained after cleanup.

## Results

| Scenario | Run terminal state | Error code | Events | Audits | Active artifacts | Cleanup |
| --- | --- | --- | ---: | ---: | ---: | --- |
| Deadline exceeded | `failed` | `sandbox_timeout` | 7 | 2 | 0 | `cleaned` |
| Memory exhausted | `failed` | `sandbox_oom` | 7 | 2 | 0 | `cleaned` |
| Launcher stopped during Run | `failed` | `launcher_unavailable` | 6 | 1 | 0 | Initial cleanup failed, recovery cleanup succeeded |

## Evidence Summary

- Timeout forced container termination and wrote `sandbox.finished`, `run.error`, terminal `run.status`, and `sandbox.cleanup`.
- OOM was read from Docker `State.OOMKilled=true`, mapped to `sandbox_oom`, and cleaned.
- Launcher outage returned only the safe message `沙箱运行服务暂不可用`; the raw transport exception, token, and Docker path were not persisted.
- Launcher restart rediscovered the container by the fixed `iap-run-{run_id}` name and removed it.
- Cleanup recovery appended a later `sandbox.cleanup={status: cleaned}` event without changing the Run's failed business terminal state.
- No failure scenario created an active Artifact record.

## Automated Regression Evidence

- Runtime, conversation, audit, artifact, application lifecycle, and platform service tests passed before staging acceptance.
- Dedicated tests cover execution-contract validation, fixed container command, safe Docker state projection, OOM mapping, timeout mapping, cancellation, Launcher outage, duplicate terminal handling, startup recovery, cleanup retry, and authorization-scoped cancellation.

## Remaining Before Production Enablement

1. Replace the staging workload worker with the complete immutable Agent/Skill/MCP/knowledge snapshot loader.
2. Execute LangGraph and DeepAgents inside the per-Run worker rather than the API process.
3. Persist checkpoints and intermediate events through authenticated platform service APIs.
4. Upload real result files to MinIO and attach Artifact records before reporting `completed`.
5. Run the same destructive suite against the complete production worker, then obtain deployment-owner approval before changing the default sandbox flag.
