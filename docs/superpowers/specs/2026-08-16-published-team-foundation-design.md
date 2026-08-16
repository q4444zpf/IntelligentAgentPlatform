# Published Multi-Agent Team Foundation Design

## 1. Goal

Add platform-managed, published multi-agent teams as a first-class execution actor. A team has one supervisor Agent and one or more member Agents. Users can manage team drafts, publish immutable versions, enable or disable teams, select a published team in the conversation console, and run it through the existing isolated LangGraph/DeepAgents execution path.

This phase closes the current gap where `actor_type="team"` is accepted by the conversation schema but is not resolved to a platform resource and is rejected by the runtime.

## 2. Scope

This design includes:

- project-scoped Team CRUD and enable/disable operations;
- draft editing and immutable published Team versions;
- one supervisor Agent plus at least one distinct member Agent;
- member roles, ordering, and optional member-specific capability whitelists;
- publication validation against published, enabled Agent resources;
- immutable Team data in each Run execution snapshot;
- bounded supervisor scheduling over existing LangGraph and DeepAgents runtimes;
- authorization through the existing Tool Gateway, approval, audit, event, checkpoint, cancellation, and Artifact contracts;
- a real collaboration management page and real Team selection in the conversation console;
- backend, frontend, migration, authorization, runtime, and end-to-end tests.

This phase does not include:

- a visual workflow or graph designer;
- shared conversations owned by a Team;
- changing Team membership after a Run starts;
- recursive Team membership or Teams containing other Teams;
- arbitrary user-authored Python nodes;
- direct device-control execution;
- an autonomous supervisor that can add unregistered Agents or capabilities;
- automatic retry of side-effecting member or Tool operations.

## 3. Architecture Decision

Use a PostgreSQL-backed Team aggregate with a mutable draft and immutable published versions. A Team Run references a concrete published version at acceptance time, and the execution snapshot embeds the complete effective Team definition needed by the Runner.

The supervisor decides which eligible member handles each subtask and may schedule bounded serial or limited-parallel work. It cannot change the Team boundary. All model, Tool, MCP, knowledge, checkpoint, event, approval, and Artifact traffic remains mediated by the existing Runner Gateway and platform services.

Rejected alternatives:

1. Frontend-only Team presets would not provide durable versions, server-side authorization, auditability, or reproducible Runs.
2. Treating a Team as a free-form DeepAgents subagent list would hide formal membership and make publication, permissions, and operational review ambiguous.
3. Building a separate multi-agent executor would duplicate cancellation, approval, audit, Artifact, and secret-isolation behavior already owned by the Sandbox Runner path.

## 4. Resource Model

### 4.1 Team

`collaboration_teams` stores the mutable resource identity and current lifecycle state:

- `id`, `unit_id`, and `project_id`;
- unique `name` within a project and optional `description`;
- `enabled`;
- `draft_revision` for optimistic concurrency;
- `published_version_id`, nullable until first publication;
- `created_by`, `updated_by`, `created_at`, and `updated_at`.

The Team row does not contain executable configuration. Draft and published definitions are separate records so updating a draft cannot alter a running or previously published version.

### 4.2 Team Version

`collaboration_team_versions` stores a complete versioned definition:

- `id`, `team_id`, and monotonically increasing `version`;
- `status`, either `draft` or `published`;
- supervisor Agent ID and its concrete published version identifier;
- member definitions and concrete published Agent version identifiers;
- Team-level Tool, Skill, and knowledge whitelists;
- `max_steps`, `max_parallel_members`, and `timeout_seconds`;
- failure strategy and approval policy references;
- `definition_digest`, publisher identity, and publication time.

Only one draft exists per Team. Published records are immutable. Publishing copies the validated draft into a new published record instead of changing the draft record in place. The Team's `published_version_id` is updated atomically.

### 4.3 Members

Each version contains normalized member records in `collaboration_team_version_members`:

- `team_version_id`, `agent_id`, and concrete Agent version identifier;
- `role`, either `supervisor` or `member`;
- a user-facing responsibility description;
- deterministic `position`;
- optional Tool, Skill, and knowledge whitelist overrides.

A version has exactly one supervisor and at least one member. An Agent may appear only once in a version. The supervisor is not duplicated as a member. Nested Teams are invalid.

## 5. Lifecycle And Validation

Create produces a disabled Team with an editable draft. Draft changes require `collaboration.manage` in the current project and the expected `draft_revision`; stale updates return `409 team_draft_conflict`.

Publication validates all of the following in one transaction:

- the Team belongs to the request unit and project;
- the supervisor and every member resolve to published, enabled Agents in the same project scope or an explicitly project-available common scope;
- supervisor and member identities are unique and membership cardinality is valid;
- every Team or member whitelist is a subset of the corresponding Agent's published bindings;
- limits are positive and do not exceed platform-configured ceilings;
- referenced Tools, Skills, knowledge sources, and model providers are published and available;
- the canonical definition fits the configured snapshot size budget.

Failure returns a stable validation code and leaves both the previous published version and draft unchanged. Successful publication creates an immutable version, advances the Team pointer, records an audit event, and retains the draft as the editable basis for the next version.

Disabling a Team prevents new Runs but does not mutate snapshots or force-cancel existing Runs. Disabling an underlying Agent or capability is still enforced at invocation time by existing platform gateways, so an in-flight Run fails closed when a required resource is no longer available.

## 6. Authorization

Public API permissions use the existing catalogue:

- `collaboration.read`: list and inspect Teams and published versions in the current project;
- `collaboration.manage`: create, edit, publish, enable, or disable Teams in the current project;
- `collaboration.run`: select an enabled Team and create a Team Run in the current project.

Unit and project identifiers come from authenticated request context, never from trusted client fields. Cross-project resources return `404` to avoid existence disclosure.

For every member capability invocation, effective authorization is the intersection of:

1. the initiating user's current unit, project membership, role permissions, and resource access;
2. the published Team version's capability whitelist;
3. the selected member Agent version's published bindings;
4. any narrower member-specific whitelist;
5. the capability's current published, enabled, availability, risk, approval, rate, and schema state.

Snapshot inclusion is necessary but not sufficient. Runner Gateway and Tool Gateway recalculate current authorization before privileged operations. Neither the supervisor prompt nor LangGraph state can grant capabilities.

## 7. Conversation And Run Resolution

For `actor_type="team"`, message acceptance requires `actor_id` and `collaboration.run`. `ConversationService` resolves the ID through `TeamService`, verifies project scope, enabled state, and a current published version, then persists both the Team actor ID and resolved version ID on the Run or its execution metadata before dispatch.

Resolution and message/Run creation occur within the same database transaction. A concurrent publish may affect later Runs but cannot change the version selected by an accepted Run. Invalid, disabled, unpublished, or inaccessible Teams return a stable `422 team_unavailable` response without persisting the user message or Run.

Team Run creation records an audit event with `resource_type="team"`, the Team ID, published version ID, initiating roles, project scope, and Run ID. Single-Agent behavior remains unchanged.

## 8. Execution Snapshot

The snapshot schema advances with a discriminated actor definition:

- an Agent actor retains the existing published Agent snapshot;
- a Team actor contains Team ID, name, version, digest, supervisor, ordered members, capability boundaries, limits, and policies;
- each supervisor/member entry contains its concrete published Agent definition, model selection, prompts, and bound resources required by the existing Agent factory;
- Team limits are clamped by platform Runner ceilings, including `max_subagents`, maximum steps, maximum parallel members, output size, and deadline.

The snapshot builder resolves the Team version recorded at Run acceptance. It must not follow the Team's current published pointer. Canonical serialization and SHA-256 digest cover the full Team definition and all embedded Agent definitions. The existing Run token and snapshot retrieval contract remains unchanged.

## 9. Runtime Scheduling

`SandboxRuntime` selects runtime construction by the snapshot actor discriminator. Team actors compile a platform-owned LangGraph supervisor graph from the immutable snapshot; the Runner does not execute persisted Python or a client-supplied graph.

The initial graph contains:

1. intake and supervisor planning;
2. a bounded task queue;
3. member execution nodes built through the existing Deep Agent factory;
4. serial execution by default and limited parallel fan-out up to `max_parallel_members`;
5. deterministic join and supervisor synthesis;
6. terminal completion, failure, cancellation, or approval interruption.

Each planned task names one member, an objective, dependency task IDs, and a bounded output contract. The runtime rejects unknown members, dependency cycles, excess steps, excess parallelism, and attempts to exceed `max_subagents`. The supervisor may revise pending tasks within remaining limits but cannot add members or capabilities.

Parallel member results are ordered by planned task position before synthesis, making event presentation and tests deterministic. The runtime does not automatically replay a member step after an uncertain side effect. Read-only failures may be retried only through existing Tool or model retry policies.

## 10. Events, Approval, Cancellation, And Artifacts

Team execution reuses the existing Run and Runner Gateway lifecycle. It adds structured events whose payloads include Team version, member Agent ID, and task ID where applicable:

- `team.plan.created`;
- `team.task.started`, `team.task.completed`, and `team.task.failed`;
- `team.synthesis.started` and `team.synthesis.completed`.

Events use existing monotonic Run sequencing and idempotency. They never include prompts containing secrets, credentials, or unrestricted model traces.

High-risk Tool calls pause through the existing approval workflow. The approval record identifies the member and Team version, while the decision remains bound to the same Run and Tool invocation. Checkpoints persist the Team graph state and snapshot digest so approval resume cannot switch Team versions.

Cancellation revokes the Run token, terminates active member work through the existing launcher path, and prevents queued tasks from starting. A required member failure follows the published failure strategy: initial scope supports `fail_fast` and `continue_then_synthesize`. The final assistant message must state partial completion when synthesis follows member failures.

Artifacts are created through the existing Artifact gateway and remain owned by the Run. Member-produced Artifacts include member and task provenance in metadata; final completion may reference only Artifacts belonging to the same Run.

## 11. API Surface

The project-scoped public API is:

- `GET /api/collaboration/teams`;
- `POST /api/collaboration/teams`;
- `GET /api/collaboration/teams/{team_id}`;
- `PATCH /api/collaboration/teams/{team_id}`;
- `PUT /api/collaboration/teams/{team_id}/draft`;
- `POST /api/collaboration/teams/{team_id}/publish`;
- `POST /api/collaboration/teams/{team_id}/enable`;
- `POST /api/collaboration/teams/{team_id}/disable`;
- `GET /api/collaboration/teams/{team_id}/versions`;
- `GET /api/collaboration/teams/{team_id}/versions/{version}`.

List responses expose lifecycle state, current version, supervisor, member count, enabled state, and update time. Draft definitions are visible only to callers with `collaboration.manage`; readers receive published definitions only. Mutation endpoints return stable errors for conflict, invalid definition, unavailable resource, and forbidden operation.

## 12. Frontend Experience

`/collaboration` becomes a real Team directory and management view. It supports searching, lifecycle and enabled filters, creation, draft editing, supervisor/member selection from real published Agents, member responsibilities, bounded runtime settings, validation feedback, publication, and enable/disable actions. Published version history is read-only.

The conversation console enables the Team mode button. It loads enabled published Teams from the API, removes fixed placeholder Team and member data, shows the selected Team's supervisor and members, and sends `actor_type="team"` with the selected Team ID. An empty Team catalogue shows a direct empty state without inventing sample resources.

Run progress renders member and task events using existing SSE data. Approval, cancellation, final content, and Artifact interactions remain shared with single-Agent Runs. Permission-gated actions are hidden or disabled consistently with existing frontend permission patterns.

## 13. Failure Handling

Stable initial error codes include:

- `team_not_found` for a scoped lookup miss;
- `team_unavailable` for disabled or unpublished Team selection;
- `team_draft_conflict` for optimistic concurrency failure;
- `team_definition_invalid` with field-level validation details;
- `team_resource_unavailable` when a referenced Agent or capability cannot be used;
- `team_plan_invalid` for runtime plans outside the published boundary;
- `team_limit_exceeded` for steps, parallelism, subagents, output, or deadline limits.

Public errors are sanitized. Publication is atomic. Snapshot creation is idempotent per Run. Runner writes retain existing idempotency requirements. A crash restores only a checkpoint matching the immutable snapshot digest.

## 14. Testing And Acceptance

Backend tests cover:

- migration creation, constraints, indexes, rollback, and PostgreSQL compatibility;
- project isolation and `collaboration.read/manage/run` enforcement;
- CRUD, optimistic concurrency, publish immutability, version increments, enable/disable, and audit records;
- publication rejection for invalid membership, unavailable Agents, invalid capability subsets, and excessive limits;
- conversation resolution without partial persistence on failure;
- snapshot stability across later Team edits or publication;
- effective authorization intersection and fail-closed resource disablement;
- supervisor plan validation, serial and bounded parallel execution, deterministic joins, failure strategies, approval resume, cancellation, and Artifact provenance;
- regression coverage for existing single-Agent Runs.

Frontend tests cover:

- Team directory loading, filters, permission states, draft validation, publication, and enable/disable flows;
- real Agent selection and removal of placeholder data;
- enabled Team mode, correct message payload, empty state, member progress, cancellation, approval, errors, and Artifacts;
- responsive layouts without overlapping controls or truncated labels.

End-to-end acceptance creates and publishes a Team with one supervisor and two members, runs a project-scoped conversation, observes at least two member task events and a synthesis event, exercises one approved Tool call, verifies cancellation of a separate Run, confirms Artifact provenance, and proves that an unauthorized user and a Team from another project cannot be selected or invoked.

## 15. Delivery Boundary

Implementation should be delivered in independently tested commits for persistence and API, publication and snapshot integration, runtime execution, frontend management, conversation experience, and end-to-end acceptance. Each commit must stage only files belonging to this feature and must not absorb unrelated workspace changes.
