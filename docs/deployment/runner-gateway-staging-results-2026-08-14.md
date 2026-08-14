# Runner Gateway Staging Results - 2026-08-14

## Decision

**Automated acceptance: PASS. Production enablement: NOT APPROVED.**

The complete backend suite and all three deployment images passed locally. A
deployment-owned staging signing key and Launcher token were not injected in
this workspace, so live staging fault injection and deployment-owner approval
remain pending. `IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED` remains `false` by
default.

## Automated evidence

- Full backend suite:
  `python -m pytest tests -q --import-mode=importlib -p no:cacheprovider`
  from `backend/` -> **837 passed, 38 skipped** in 661.91 seconds.
- Task 12 Runner Gateway integration suite -> **17 passed**.
- Runtime-limit and snapshot compatibility suite -> **29 passed**.
- `docker compose --profile sandbox config --quiet` -> exit code 0.
- `docker compose build api workflow-runner sandbox-launcher` -> all three
  images built successfully.

The 38 skipped tests require external services such as a configured PostgreSQL
test database. They are not counted as live staging evidence.

## Image identities

| Image | Immutable local digest |
| --- | --- |
| `intelligent-agent-platform-api:latest` | `sha256:96d7441d4277946e2dd1ef128643ca1ce719c57b45242fa44309628309fc9585` |
| `intelligent-agent-platform-workflow-runner:latest` | `sha256:d031db728ef0fba42cefb1ad5f9522ddc2d3e45c6978875216c7066a349733a4` |
| `intelligent-agent-platform-sandbox-launcher:latest` | `sha256:6d4c02eca6e587b3b818ad4ecdb430dd909c40ed77d7594836c8719e92c8712e` |

## Boundary inspection

- Compose resolves `IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED=false`.
- `runner-gateway` resolves as an internal Docker network.
- Workflow Runner joins only `runner-gateway` and has no Docker Socket mount.
- Sandbox Launcher joins only `runner-gateway`; it is the only service allowed
  to mount `/var/run/docker.sock`.
- API joins the default application network and `runner-gateway`.
- `docker ps -a --filter name=iap-run-` returned no residual Run containers.

Environment key names inspected without printing values:

- API: `DATABASE_URL`, `IAP_RUNNER_GATEWAY_URL`,
  `IAP_RUNNER_TOKEN_SIGNING_KEY`, object-storage configuration and the sandbox
  feature flag.
- Workflow Runner: `IAP_RUNNER_LAUNCHER_TOKEN`, Launcher URL, sandbox readiness
  controls, Runner Gateway network and the sandbox feature flag.
- Sandbox Launcher: `IAP_RUNNER_LAUNCHER_TOKEN`, Runner image, Runner Gateway
  URL/network and Launcher enablement flag.

The scoped Run token is carried inside the opaque `IAP_RUN_EXECUTION_REQUEST`
execution envelope because the Run process needs it to authenticate to the
Gateway. It is not exposed as a separate environment variable and must never be
printed, persisted in events or logged. Provider, MCP, database and
object-storage credentials are not placed in the Run container contract. Task
11 inspection tests enforce the Run environment allowlist
`IAP_RUN_EXECUTION_REQUEST` and `IAP_RUNNER_GATEWAY_URL`.

## Scenario results

| Scenario | Result | Evidence |
| --- | --- | --- |
| Normal status, event, model, tool, checkpoint, final message and history | PASS | `test_runner_gateway_execution.py` |
| Artifact create, download, digest and cross-Run isolation | PASS | `test_runner_gateway_artifacts.py` |
| Expired, revoked and cross-Run tokens | PASS | `test_runner_gateway_failures.py` |
| Action-scoped token denial | PASS | `test_runner_gateway_failures.py` |
| Snapshot mismatch and duplicate event/completion | PASS | `test_runner_gateway_failures.py` |
| Disabled and unauthorized tools | PASS | `test_runner_gateway_failures.py` |
| Approval interruption and approved resume | PASS | `test_runner_gateway_failures.py` |
| Model, checkpoint and Artifact failure sanitization | PASS | Task 12 integration tests |
| Artifact digest rejection and post-upload object compensation | PASS | `test_runner_gateway_artifacts.py` |
| Cancellation, deadline, OOM and Launcher outage final states | PASS (automated) | Task 12 and lifecycle tests |
| Runtime iteration, tool, subagent and output limits | PASS | Runtime-limit tests and schema v3 snapshot |
| Live staging OOM and Launcher outage fault injection | PENDING | Deployment credentials not available |
| Live Run container non-root/read-only/capability inspection | PENDING | No staging Run container launched |

## Runtime limits

New schema v3 snapshots immutably carry these defaults:

- `IAP_RUNNER_MAX_ITERATIONS=4`
- `IAP_RUNNER_MAX_TOOL_CALLS=8`
- `IAP_RUNNER_MAX_SUBAGENTS=4`
- `IAP_RUNNER_MAX_OUTPUT_BYTES=4194304`

Legacy schema v1/v2 digest verification remains compatible. New limit fields
are digest-protected in schema v3.

## Cleanup and rollback

Automated failure tests verify Artifact digest rejection, database rollback and
object deletion when event persistence fails after upload, plus idempotent
terminal persistence. The local Docker inventory had no `iap-run-*` containers
after the suite.

Rollback remains:

1. Keep or restore `IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED=false`.
2. Restart API and Workflow Runner.
3. Cancel active Runs to revoke Run tokens and terminate containers.
4. Confirm no `iap-run-*` container remains.
5. Remove sandbox profile services only after active Runs are clear.

## Residual risks and required approval

- Execute live staging success, unauthorized tool, approval, cancellation,
  timeout, OOM and Launcher outage scenarios with deployment-owned secrets.
- Inspect an active Run container's user, read-only root, capabilities, mounts,
  network and environment key names.
- Run PostgreSQL-only integration tests against the staging-compatible database.
- Record the deployment owner's name and approval for service identity, secret
  injection, network policy and rollback.

Until these items are recorded, sandbox execution must remain disabled in the
production deployment.
