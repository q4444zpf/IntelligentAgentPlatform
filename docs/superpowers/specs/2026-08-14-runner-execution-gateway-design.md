# Workflow Runner Execution Gateway Design

## 1. Goal

Move the real LangGraph and DeepAgents execution path into the isolated per-Run Workflow Runner without giving the Runner direct PostgreSQL access, MinIO administrator credentials, Docker access, model-provider secrets, or MCP credentials.

The platform remains the authority for configuration, permissions, approvals, audit, persistence, and external capability access. The Runner receives an immutable execution snapshot and a short-lived, Run-scoped service token, executes only the authorized graph and agent definitions, and sends results back through internal platform APIs.

## 2. Scope

This design covers:

- immutable execution snapshots for Agent, Prompt, Skill, MCP/Tool, knowledge and runtime limits;
- short-lived Run tokens with Run, action and expiration scope;
- internal Runner Gateway APIs;
- DeepAgents and LangGraph creation inside the per-Run container;
- model, Skill, MCP and knowledge invocation through platform gateways;
- intermediate events, checkpoints, final messages and Artifact uploads;
- cancellation, timeout, OOM, Launcher outage and cleanup integration;
- staging acceptance and production enablement gates.

This design does not add arbitrary user Python nodes, direct Shell execution inside Workflow Runner, direct device-control execution, unrestricted outbound network access, or direct Runner access to platform databases and storage credentials.

## 3. Architecture Decision

Use a short-lived Run token and an internal execution gateway.

The scheduling side creates an immutable execution snapshot, stores it as a platform-controlled object, and submits only the Run ID, snapshot reference, snapshot digest, token and deadline to the sandbox execution plane. The Runner validates the snapshot, creates the approved LangGraph/DeepAgents runtime, and calls platform-controlled APIs for every privileged operation.

The alternatives were rejected:

1. Direct PostgreSQL and MinIO access from Runner would reduce implementation work but distribute long-lived credentials into per-Run containers and broaden cross-Run access risk.
2. Embedding model and MCP credentials in the snapshot or container environment would make external calls simple but would expose reusable secrets to the execution environment.
3. Running Tool Gateway and persistence logic locally inside Runner would duplicate authorization and audit behavior and allow stale snapshot permissions to bypass real-time disablement or approval policies.

## 4. Trust Boundaries

### 4.1 Platform Core

The platform core owns:

- published Agent, Skill, Tool, MCP and workflow definitions;
- user, project and role authorization;
- current tool availability and publication state;
- approval policy and decisions;
- model and external-service credentials;
- Run, RunEvent, checkpoint, audit and Artifact persistence;
- MinIO object creation and download authorization.

### 4.2 Sandbox Launcher

The Launcher owns Docker lifecycle only. It accepts a validated Run execution envelope from Workflow Runner, applies the fixed container policy, starts or terminates the trusted image, reports sanitized container state and removes the container.

It does not receive database credentials, model credentials, MCP credentials or user session cookies.

### 4.3 Workflow Runner

The per-Run container receives:

- Run ID;
- immutable snapshot object reference and SHA-256 digest;
- short-lived Run token;
- internal Runner Gateway base URL;
- deadline and bounded runtime settings.

It does not receive:

- PostgreSQL credentials;
- MinIO access and secret keys;
- Docker Socket;
- model-provider API keys;
- MCP credentials;
- another Run's token or workspace;
- arbitrary image, mount or command configuration.

## 5. Immutable Execution Snapshot

`ExecutionSnapshotService` builds a canonical JSON snapshot after the message and Run have been committed. The snapshot is immutable for the lifetime of the Run and includes explicit published version identifiers.

The snapshot contains:

- schema version and snapshot ID;
- Run, unit, project and initiating user references;
- actor type, Agent or team version and prompt content;
- model provider and model identifiers without provider secrets;
- bound Skill metadata and approved Skill content references;
- published Tool definitions, JSON Schemas, versions and risk metadata;
- MCP tool identities without transport credentials;
- knowledge-source identities and permitted query scopes;
- conversation messages visible to the Run;
- approval rules, filesystem policy and runtime limits;
- maximum iterations, tool calls, subagents, output size and deadline;
- snapshot creation time and SHA-256 digest.

Canonical serialization uses UTF-8 JSON with sorted keys and deterministic separators. The digest covers the complete canonical payload. The Runner rejects missing, expired, malformed or digest-mismatched snapshots before model or tool execution.

The object key is scoped by unit, project and Run. The Runner does not download it from MinIO directly. It requests the snapshot from Runner Gateway with the Run token, and the platform reads and verifies the object.

## 6. Run Token

`RunTokenService` issues a cryptographically signed, short-lived token after snapshot persistence. The token is not a user login token and cannot be used against public Web APIs.

Required claims:

- issuer and audience dedicated to Runner Gateway;
- unique token ID;
- Run ID, unit ID and project ID;
- snapshot ID and digest;
- allowed internal actions;
- issued-at, not-before and expiration timestamps.

Allowed actions are explicit values such as:

- `snapshot.read`;
- `model.invoke`;
- `tool.invoke`;
- `checkpoint.read` and `checkpoint.write`;
- `event.append`;
- `artifact.create`;
- `result.complete`.

The token lifetime is bounded by the Run deadline with a short grace period. The platform maintains a revocation record keyed by token ID and Run ID. Cancellation, terminal completion or administrative termination revokes the token. Every Runner Gateway request checks signature, audience, expiration, revocation, Run scope and action scope.

Tokens are accepted only through the internal service network. They are redacted from logs, audit metadata, exception text and persisted events.

## 7. Runner Gateway API

Runner Gateway is an internal FastAPI router under a non-public service path. Nginx does not expose it to browsers. All endpoints require the Run token and derive Run scope only from validated token claims.

The first implementation provides:

- `GET /internal/runner/runs/{run_id}/snapshot`;
- `POST /internal/runner/runs/{run_id}/model-invocations`;
- `POST /internal/runner/runs/{run_id}/tool-invocations`;
- `GET /internal/runner/runs/{run_id}/checkpoints/latest`;
- `PUT /internal/runner/runs/{run_id}/checkpoints/{checkpoint_key}`;
- `POST /internal/runner/runs/{run_id}/events`;
- `POST /internal/runner/runs/{run_id}/artifacts`;
- `POST /internal/runner/runs/{run_id}/complete`.

Requests must include an idempotency key for model, tool, checkpoint, event, Artifact and completion writes. Cross-Run URL/token combinations return `404`; valid Run tokens lacking an action return `403`. Responses contain stable business error codes and safe messages, never raw credential, network or storage exceptions.

## 8. Runtime Construction

`SandboxRuntime` performs these steps inside the container:

1. Parse the fixed execution envelope.
2. Fetch and verify the immutable snapshot.
3. Restore the latest checkpoint when present.
4. Create gateway-backed model, tool, checkpoint, event and Artifact clients.
5. Build the published Deep Agent from the snapshot.
6. Wrap the Agent in the required LangGraph graph or load the published workflow graph.
7. Invoke the graph with `thread_id=run_id` and bounded runtime metadata.
8. Stream intermediate events and checkpoints through Runner Gateway.
9. Upload final files and send the terminal result.
10. Exit with a sanitized status code so the coordinator can reconcile container state.

The Runner does not import platform database stores or construct `ToolGateway` with database access. It uses gateway adapters only.

## 9. DeepAgents Integration

`DeepAgentFactory` builds `create_deep_agent` from immutable snapshot data.

The runtime uses a composite backend:

- the default workspace uses DeepAgents `StateBackend` for ephemeral scratch files persisted through LangGraph checkpoints;
- `/artifacts/` uses a platform `ArtifactBackend` that calls Runner Gateway;
- future `/memories/` support may use a dedicated platform store after long-term-memory authorization is designed;
- no local host filesystem backend is exposed.

Skill packages are progressively loaded from snapshot-approved content. Skill instructions may shape behavior but do not grant capabilities. Executable Skill actions are represented as authorized tools and are invoked through GatewayToolAdapter.

Subagents inherit the intersection of the parent Agent snapshot, initiating user permissions and team whitelist. They cannot add tools, knowledge sources, MCP clients or filesystem routes.

## 10. Model, Tool, MCP And Knowledge Calls

### 10.1 Model Gateway

The Runner sends normalized messages, selected model identity, tool definitions and bounded generation settings to Runner Gateway. The platform resolves provider credentials, invokes the configured LLM, records usage and audit, and returns a normalized model result.

### 10.2 GatewayToolAdapter

The adapter exposes snapshot-authorized tools in the DeepAgents/LangChain tool format. Each invocation sends the tool ID, version, arguments, tool-call ID and idempotency key to Runner Gateway.

The platform recalculates authorization using:

- initiating user and current project membership;
- current Agent or team version authorization;
- snapshot tool version;
- current publication, enabled and source-availability state;
- Tool risk, approval, schema, rate and timeout policy.

Snapshot inclusion is necessary but not sufficient. A tool disabled after Run creation fails closed. High-risk calls pause through the existing approval workflow. The Runner stores the interruption checkpoint and exits or waits according to the workflow policy.

MCP and knowledge calls remain Tool Gateway execution adapters. The Runner never receives MCP credentials or vector-store credentials.

## 11. Events, Checkpoints And Results

Runner events use monotonically increasing Runner-local sequence numbers and idempotency keys. The platform maps them into RunEvent records under a locked Run sequence. Supported initial event categories include:

- planning and step status;
- model invocation progress;
- tool invocation status;
- approval or input interruption;
- message delta and completion;
- Artifact creation and readiness;
- safe runtime errors.

Checkpoint payloads must be JSON serializable, size bounded and associated with the snapshot digest. Checkpoint writes are idempotent by Run ID and checkpoint key. Restore rejects checkpoints created from a different snapshot digest.

The terminal completion request contains:

- terminal status;
- final assistant content;
- usage totals;
- final checkpoint key;
- Artifact references;
- safe error code when failed or cancelled.

The platform validates that referenced Artifacts belong to the same Run before committing the final message and terminal status in one transaction.

## 12. Artifact Backend

`ArtifactBackend` presents `/artifacts/` as a virtual DeepAgents filesystem. It does not expose MinIO keys or credentials.

Writes are create-only for the first implementation. The backend sends the virtual path, content type, byte length and content to Runner Gateway. The platform:

- validates path, size and content type;
- creates the MinIO object under the current unit/project/Run prefix;
- writes the Artifact database record;
- appends an `artifact.created` or `artifact.ready` event;
- returns the Artifact ID and virtual path.

Reads and listings are restricted to active Artifacts attached to the same Run. Explicit deletion remains a platform-user operation and is not granted to the Runner token in the first implementation.

## 13. Failure Handling

The execution path fails closed. It never falls back to API-process or host execution when sandbox or gateway capability is unavailable.

Stable failure codes include:

- `snapshot_unavailable`;
- `snapshot_invalid`;
- `run_token_invalid`;
- `run_token_expired`;
- `runner_gateway_unavailable`;
- `model_request_failed`;
- `tool_not_authorized`;
- `tool_approval_required`;
- `tool_execution_failed`;
- `checkpoint_failed`;
- `artifact_upload_failed`;
- existing `sandbox_timeout`, `sandbox_oom`, `sandbox_cancelled`, `launcher_unavailable` and `sandbox_failed`.

The coordinator remains authoritative for deadline, cancellation, OOM, Launcher outage, token revocation and container cleanup. Runner Gateway completion and Docker exit reconciliation are idempotent so duplicate terminal notifications cannot create conflicting states.

## 14. Security Requirements

- Runner has no database credentials, MinIO administrator keys, Docker Socket, provider keys or MCP secrets.
- Runner outbound network is limited to Runner Gateway. The current `network=none` staging policy must evolve to a dedicated internal-only network before real gateway calls are enabled.
- Run tokens are short-lived, action scoped, Run scoped, revocable and never persisted in RunEvent or audit metadata.
- Snapshot and checkpoint sizes are bounded before allocation.
- Tool arguments and results use existing schema validation and redaction.
- Artifact virtual paths reject traversal, drive letters, backslashes, NUL bytes and cross-prefix access.
- The trusted image contains only platform code and pinned dependencies; users cannot select modules, commands or images.
- Logs identify requests by Run ID and trace ID without logging authorization headers or raw upstream errors.

## 15. Testing Strategy

### 15.1 Unit Tests

- canonical snapshot serialization, immutability and SHA-256 verification;
- Run token signature, audience, Run/action scope, expiration and revocation;
- internal endpoint `403` and `404` behavior;
- snapshot tool whitelist and real-time disablement;
- virtual Artifact path and size validation;
- safe error mapping and sensitive-data redaction;
- runtime construction from a snapshot without database-backed stores.

### 15.2 Integration Tests

- Runner fetches a snapshot and creates DeepAgents/LangGraph;
- model invocation passes through platform Model Gateway;
- authorized Skill/MCP tool invocation passes through Tool Gateway;
- unauthorized and disabled tools fail closed;
- approval interruption saves a checkpoint and resumes;
- intermediate events remain ordered and idempotent;
- result files become downloadable MinIO-backed Artifacts;
- history shows Run time, Agent, status and safe error details.

### 15.3 Exceptional Scenarios

- expired or revoked token;
- snapshot digest mismatch;
- Runner Gateway or model timeout;
- MCP offline;
- checkpoint write failure;
- Artifact upload failure;
- cancellation, deadline, OOM and Launcher outage;
- duplicate completion and duplicate event requests;
- cross-Run token, Artifact or checkpoint access.

### 15.4 Staging Acceptance

Run a normal task, an authorized tool call, an unauthorized tool call, file generation, intermediate-process viewing, final-file download, history query, cancellation, timeout, OOM and Launcher outage. Confirm no secrets or internal host paths appear and no `iap-run-*` containers remain.

## 16. Enablement Gate

`IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED=false` remains the default until all of these are true:

1. The production Runner executes the real immutable snapshot through DeepAgents and LangGraph.
2. Model, Skill, MCP and knowledge calls use platform gateways.
3. Checkpoint, event and Artifact integration tests pass.
4. Runner image inspection confirms no database, storage, provider, MCP or Docker credentials.
5. The complete real staging acceptance suite passes.
6. The deployment owner approves sandbox enablement and provisions production service identity and network policy.

## 17. Delivery Sequence

Implementation is split into independently verifiable increments:

1. snapshot model, canonical storage and digest;
2. Run token issuance and internal authentication;
3. snapshot, event and checkpoint Runner Gateway endpoints;
4. gateway-backed model and tool adapters;
5. DeepAgents/LangGraph runtime assembly inside Runner;
6. Artifact virtual backend and final-result transaction;
7. cancellation, approval and restart recovery;
8. real staging acceptance and production enablement review.
