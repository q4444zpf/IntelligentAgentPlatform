# Workflow Runner Execution Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the real LangGraph/DeepAgents runtime inside one isolated container per Run while every privileged model, tool, checkpoint, event and Artifact operation remains mediated by the platform.

**Architecture:** The API process creates an immutable, digest-bound execution snapshot and a short-lived action-scoped Run token. The container receives only an execution envelope, fetches the snapshot through an internal FastAPI Runner Gateway, constructs gateway-backed DeepAgents/LangGraph adapters, and reports all state through idempotent internal endpoints. The coordinator remains authoritative for terminal reconciliation, token revocation, deadline enforcement and container cleanup.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, PostgreSQL 16, Alembic, LangGraph, DeepAgents, LangChain Core, MinIO/S3, Docker Compose, pytest.

## Global Constraints

- Keep `IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED=false` as the default until the complete staging gate passes.
- Runner containers must never receive PostgreSQL credentials, MinIO credentials, provider keys, MCP credentials, Docker Socket access or user session cookies.
- Runner tokens must be signed, short-lived, Run-scoped, action-scoped, revocable and redacted from logs, events, audits and exceptions.
- Snapshot inclusion is necessary but not sufficient for tool execution; current publication, enablement, source availability, user permission and approval policy must be recalculated by the platform.
- The execution path must fail closed and must not fall back to API-process or host execution when sandbox or gateway capability is unavailable.
- Snapshot and checkpoint payloads must be canonical JSON, digest-bound, JSON serializable and size bounded before persistence.
- Artifact paths must reject traversal, drive letters, backslashes and NUL bytes; Runner Artifact access is scoped to the current Run.
- The initial Runner Artifact interface is create/read/list only; deletion remains a public platform-user operation.
- All mutating Runner Gateway requests require an idempotency key.
- Existing public `/api/*` contracts and the current non-sandbox Harness remain unchanged until the enablement gate is approved.

---

## File Structure

- `backend/app/runtime/execution_snapshot.py`: snapshot schemas, canonical serialization, digest verification and persistence service.
- `backend/app/runtime/run_tokens.py`: token claims, HMAC signing, validation and revocation service.
- `backend/app/runtime/runner_gateway_auth.py`: FastAPI dependency that authenticates and scopes internal requests.
- `backend/app/runtime/runner_gateway_schemas.py`: request/response contracts shared by internal endpoints and Runner clients.
- `backend/app/runtime/runner_gateway_service.py`: platform-side snapshot, model, tool, checkpoint, event, Artifact and completion orchestration.
- `backend/app/runtime/runner_gateway_router.py`: internal-only FastAPI routes under `/internal/runner`.
- `backend/app/runtime/runner_gateway_client.py`: container-side HTTP client with safe error mapping and idempotency headers.
- `backend/app/runtime/gateway_model.py`: LangChain-compatible model adapter backed by Runner Gateway.
- `backend/app/runtime/gateway_tools.py`: DeepAgents/LangChain tool definitions backed by Runner Gateway.
- `backend/app/runtime/artifact_backend.py`: virtual `/artifacts/` backend backed by platform Artifact APIs.
- `backend/app/runtime/sandbox_runtime.py`: snapshot validation, DeepAgents/LangGraph construction, checkpoint restoration and completion.
- Existing runtime, conversation, Artifact, launcher, compose and migration files are modified only at their current ownership boundaries.

---

### Task 1: Immutable Execution Snapshot

**Files:**
- Create: `backend/app/runtime/execution_snapshot.py`
- Create: `backend/tests/runtime/test_execution_snapshot.py`
- Create: `backend/alembic/versions/20260814_18_execution_snapshots.py`
- Modify: `backend/app/db/base.py`
- Modify: `backend/tests/integration/test_postgres_migrations.py`

**Interfaces:**
- Consumes: `AgentService.get(agent_id) -> AgentInfo`, `ConversationRepository.get_run_execution_context(run_id)`, `ConversationRepository.get_run_messages(run_id)`.
- Produces: `ExecutionSnapshotService.create(run_id: str) -> StoredExecutionSnapshot`, `ExecutionSnapshotService.get(snapshot_id: str) -> StoredExecutionSnapshot | None`, `canonical_snapshot_bytes(payload: ExecutionSnapshotPayload) -> bytes`, `verify_snapshot_digest(payload, digest) -> bool`.

- [ ] **Step 1: Write failing canonicalization and persistence tests**

```python
def test_snapshot_digest_is_deterministic_and_covers_complete_payload(snapshot_service):
    first = snapshot_service.create("run-1")
    second_bytes = canonical_snapshot_bytes(first.payload)
    assert hashlib.sha256(second_bytes).hexdigest() == first.digest
    assert verify_snapshot_digest(first.payload, first.digest)

def test_snapshot_contains_no_provider_or_mcp_secrets(snapshot_service):
    stored = snapshot_service.create("run-1")
    serialized = canonical_snapshot_bytes(stored.payload).decode("utf-8")
    assert "provider-secret" not in serialized
    assert "mcp-secret" not in serialized
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `python -m pytest backend/tests/runtime/test_execution_snapshot.py -v`

Expected: FAIL because `app.runtime.execution_snapshot` and the snapshot table do not exist.

- [ ] **Step 3: Add snapshot contracts and canonical digest implementation**

```python
class ExecutionSnapshotPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1"] = "1"
    snapshot_id: str
    run_id: str
    unit_id: str
    project_id: str
    user_id: str
    actor: PublishedAgentSnapshot
    model: SnapshotModelSelection
    messages: tuple[SnapshotMessage, ...]
    skills: tuple[SnapshotSkill, ...] = ()
    knowledge_sources: tuple[SnapshotKnowledgeSource, ...] = ()
    limits: SnapshotRuntimeLimits
    created_at: datetime

def canonical_snapshot_bytes(payload: ExecutionSnapshotPayload) -> bytes:
    return json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
```

Persist `snapshot_id`, `run_id` (unique), `digest`, `payload`, `created_at` and `expires_at` in `runtime_execution_snapshots`. Build the payload only from published identifiers/content and non-secret model/MCP identities. Reject disabled Agents and snapshots larger than `IAP_RUNNER_SNAPSHOT_MAX_BYTES`, default `1048576`.

- [ ] **Step 4: Add migration upgrade/downgrade assertions**

Extend the PostgreSQL migration test to upgrade to `20260814_18`, assert `runtime_execution_snapshots` and its unique Run index exist, downgrade to `20260812_17`, then re-upgrade.

- [ ] **Step 5: Run snapshot and migration tests**

Run: `python -m pytest backend/tests/runtime/test_execution_snapshot.py backend/tests/integration/test_postgres_migrations.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/runtime/execution_snapshot.py backend/tests/runtime/test_execution_snapshot.py backend/alembic/versions/20260814_18_execution_snapshots.py backend/app/db/base.py backend/tests/integration/test_postgres_migrations.py
git commit -m "feat: add immutable run execution snapshots"
```

### Task 2: Short-Lived Run Token Service

**Files:**
- Create: `backend/app/runtime/run_tokens.py`
- Create: `backend/tests/runtime/test_run_tokens.py`
- Create: `backend/alembic/versions/20260814_19_run_token_revocations.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/tests/integration/test_postgres_migrations.py`

**Interfaces:**
- Consumes: `StoredExecutionSnapshot.snapshot_id`, `.run_id`, `.digest`, `.expires_at`.
- Produces: `RunTokenService.issue(snapshot, actions, deadline_at) -> IssuedRunToken`, `RunTokenService.verify(token, run_id, required_action) -> RunTokenClaims`, `RunTokenService.revoke(run_id, reason) -> None`.

- [ ] **Step 1: Write failing token security tests**

```python
def test_token_is_run_action_and_digest_scoped(token_service, snapshot):
    issued = token_service.issue(snapshot, {"snapshot.read"}, snapshot.expires_at)
    claims = token_service.verify(issued.value, "run-1", "snapshot.read")
    assert claims.snapshot_digest == snapshot.digest
    with pytest.raises(RunTokenForbidden):
        token_service.verify(issued.value, "run-1", "tool.invoke")
    with pytest.raises(RunTokenNotFound):
        token_service.verify(issued.value, "run-2", "snapshot.read")

def test_revoked_and_expired_tokens_are_rejected(token_service, snapshot):
    issued = token_service.issue(snapshot, {"snapshot.read"}, snapshot.expires_at)
    token_service.revoke(snapshot.run_id, "cancelled")
    with pytest.raises(RunTokenInvalid):
        token_service.verify(issued.value, snapshot.run_id, "snapshot.read")
```

- [ ] **Step 2: Run tests and verify missing implementation failure**

Run: `python -m pytest backend/tests/runtime/test_run_tokens.py -v`

Expected: FAIL because the token service is absent.

- [ ] **Step 3: Implement signed token claims and revocation persistence**

Use HMAC-SHA256 through `cryptography.hazmat.primitives.hmac.HMAC`; encode header and claims using unpadded URL-safe Base64. Required claims are `iss`, `aud`, `jti`, `run_id`, `unit_id`, `project_id`, `snapshot_id`, `snapshot_digest`, `actions`, `iat`, `nbf`, and `exp`. Configure `IAP_RUNNER_TOKEN_SIGNING_KEY`, `IAP_RUNNER_TOKEN_ISSUER=iap-api`, `IAP_RUNNER_TOKEN_AUDIENCE=iap-runner-gateway`, and `IAP_RUNNER_TOKEN_GRACE_SECONDS=30`; startup must refuse Runner Gateway enablement when the signing key is absent or shorter than 32 bytes.

Persist `jti`, `run_id`, `revoked_at` and `reason` in `runtime_run_token_revocations`; verification checks both `jti` and Run revocation.

- [ ] **Step 4: Add redaction assertions**

```python
def test_token_errors_never_include_raw_token(token_service, snapshot):
    raw = token_service.issue(snapshot, {"snapshot.read"}, snapshot.expires_at).value
    with pytest.raises(RunTokenInvalid) as captured:
        token_service.verify(raw + "broken", snapshot.run_id, "snapshot.read")
    assert raw not in str(captured.value)
```

- [ ] **Step 5: Run token and migration tests**

Run: `python -m pytest backend/tests/runtime/test_run_tokens.py backend/tests/integration/test_postgres_migrations.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/runtime/run_tokens.py backend/tests/runtime/test_run_tokens.py backend/alembic/versions/20260814_19_run_token_revocations.py backend/app/core/config.py backend/tests/integration/test_postgres_migrations.py
git commit -m "feat: add scoped runner tokens"
```

### Task 3: Internal Authentication And Snapshot Endpoint

**Files:**
- Create: `backend/app/runtime/runner_gateway_auth.py`
- Create: `backend/app/runtime/runner_gateway_schemas.py`
- Create: `backend/app/runtime/runner_gateway_service.py`
- Create: `backend/app/runtime/runner_gateway_router.py`
- Create: `backend/tests/runtime/test_runner_gateway_auth.py`
- Create: `backend/tests/runtime/test_runner_gateway_api.py`
- Modify: `backend/app/main.py`
- Modify: `frontend/nginx.conf`

**Interfaces:**
- Consumes: `RunTokenService.verify`, `ExecutionSnapshotService.get`.
- Produces: `require_runner_action(action: RunnerAction)`, `GET /internal/runner/runs/{run_id}/snapshot -> SnapshotResponse`.

- [ ] **Step 1: Write failing endpoint authorization tests**

```python
def test_snapshot_endpoint_returns_verified_payload(client, token, snapshot):
    response = client.get(
        f"/internal/runner/runs/{snapshot.run_id}/snapshot",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["digest"] == snapshot.digest

def test_cross_run_is_404_and_missing_action_is_403(client, snapshot_token, model_only_token):
    assert client.get("/internal/runner/runs/run-2/snapshot", headers=bearer(snapshot_token)).status_code == 404
    assert client.get("/internal/runner/runs/run-1/snapshot", headers=bearer(model_only_token)).status_code == 403
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `python -m pytest backend/tests/runtime/test_runner_gateway_auth.py backend/tests/runtime/test_runner_gateway_api.py -v`

Expected: FAIL with route not found.

- [ ] **Step 3: Implement the internal auth dependency and snapshot service method**

```python
def require_runner_action(action: RunnerAction):
    def dependency(run_id: str, authorization: str = Header(default="")) -> RunTokenClaims:
        token = parse_bearer_token(authorization)
        return token_service.verify(token, run_id, action)
    return dependency
```

Return stable bodies: `{"code":"run_token_invalid","message":"Runner 凭证无效"}`, `{"code":"run_token_expired",...}`, `{"code":"runner_action_forbidden",...}`, and `{"code":"run_not_found",...}`. Before returning the snapshot, recalculate its canonical digest and reject mismatch as `409 snapshot_invalid`.

- [ ] **Step 4: Register the internal router and keep it out of Nginx**

Register with `app.include_router(runner_gateway_router, prefix="/internal/runner", include_in_schema=False)`. Add an explicit Nginx block before `/api/`:

```nginx
location /internal/ {
  return 404;
}
```

- [ ] **Step 5: Run endpoint, main-app and Nginx boundary tests**

Run: `python -m pytest backend/tests/runtime/test_runner_gateway_auth.py backend/tests/runtime/test_runner_gateway_api.py backend/tests/test_main.py backend/tests/runtime/test_launcher_compose_boundary.py -v`

Expected: PASS, including a test that browser-facing Nginx never proxies `/internal/runner`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/runtime/runner_gateway_auth.py backend/app/runtime/runner_gateway_schemas.py backend/app/runtime/runner_gateway_service.py backend/app/runtime/runner_gateway_router.py backend/tests/runtime/test_runner_gateway_auth.py backend/tests/runtime/test_runner_gateway_api.py backend/app/main.py frontend/nginx.conf
git commit -m "feat: expose internal runner snapshot gateway"
```

### Task 4: Idempotent Events And Digest-Bound Checkpoints

**Files:**
- Modify: `backend/app/runtime/checkpoint_store.py`
- Modify: `backend/app/runtime/runner_gateway_schemas.py`
- Modify: `backend/app/runtime/runner_gateway_service.py`
- Modify: `backend/app/runtime/runner_gateway_router.py`
- Create: `backend/alembic/versions/20260814_20_runner_idempotency.py`
- Create: `backend/tests/runtime/test_runner_gateway_state.py`
- Modify: `backend/tests/runtime/test_checkpoint_store.py`

**Interfaces:**
- Produces: `GET .../checkpoints/latest`, `PUT .../checkpoints/{checkpoint_key}`, `POST .../events`; `CheckpointStore.save(run_id, checkpoint_key, state, snapshot_digest, idempotency_key)`.

- [ ] **Step 1: Write failing checkpoint and event tests**

```python
def test_checkpoint_restore_rejects_other_snapshot_digest(client, token):
    put_checkpoint(client, token, digest="a" * 64, key="step-1")
    response = get_latest_checkpoint(client, token, digest="b" * 64)
    assert response.status_code == 409
    assert response.json()["code"] == "checkpoint_snapshot_mismatch"

def test_duplicate_event_idempotency_key_creates_one_event(client, token, repository):
    post_event(client, token, key="evt-1", sequence=1)
    post_event(client, token, key="evt-1", sequence=1)
    assert len(repository.list_events("run-1", 0)) == 1
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `python -m pytest backend/tests/runtime/test_runner_gateway_state.py backend/tests/runtime/test_checkpoint_store.py -v`

Expected: FAIL because digest and idempotency fields are unsupported.

- [ ] **Step 3: Extend persistence models and services**

Add `snapshot_digest` and `idempotency_key` to `runtime_checkpoints`, and create `runtime_runner_requests(run_id, action, idempotency_key, response_json, created_at)` with a unique constraint on `(run_id, action, idempotency_key)`. Lock the `AgentRun` row before translating Runner-local sequence into the next platform `RunEvent.sequence`. Limit checkpoint JSON to `IAP_RUNNER_CHECKPOINT_MAX_BYTES`, default `2097152`, and event payload JSON to `65536` bytes.

- [ ] **Step 4: Implement stable endpoint semantics**

The write endpoints require `Idempotency-Key`; a repeated key returns the stored response with status `200`, a reused key with different request digest returns `409 idempotency_conflict`, and a sequence gap returns `409 event_sequence_invalid`.

- [ ] **Step 5: Run focused and conversation repository tests**

Run: `python -m pytest backend/tests/runtime/test_runner_gateway_state.py backend/tests/runtime/test_checkpoint_store.py backend/tests/conversations/test_repository.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/runtime/checkpoint_store.py backend/app/runtime/runner_gateway_schemas.py backend/app/runtime/runner_gateway_service.py backend/app/runtime/runner_gateway_router.py backend/alembic/versions/20260814_20_runner_idempotency.py backend/tests/runtime/test_runner_gateway_state.py backend/tests/runtime/test_checkpoint_store.py
git commit -m "feat: add runner events and checkpoints"
```

### Task 5: Model Invocation Gateway And Container Adapter

**Files:**
- Create: `backend/app/runtime/gateway_model.py`
- Create: `backend/tests/runtime/test_gateway_model.py`
- Modify: `backend/app/runtime/runner_gateway_schemas.py`
- Modify: `backend/app/runtime/runner_gateway_service.py`
- Modify: `backend/app/runtime/runner_gateway_router.py`
- Modify: `backend/tests/runtime/test_runner_gateway_api.py`

**Interfaces:**
- Consumes: existing `ModelGateway.generate(messages, selection, tools=...) -> ModelResult`.
- Produces: `POST .../model-invocations -> ModelInvocationResponse`, `GatewayChatModel.invoke(messages, tools) -> AIMessage`.

- [ ] **Step 1: Write failing model mediation tests**

```python
def test_model_endpoint_uses_snapshot_selection_not_request_override(client, token, fake_model):
    response = invoke_model(client, token, requested_model="attacker-model")
    assert response.status_code == 200
    assert fake_model.selections == [ModelSelection("approved-provider", "approved-model")]

def test_gateway_chat_model_normalizes_tool_calls(http_transport):
    model = GatewayChatModel(http_transport)
    message = model.invoke([HumanMessage(content="查询水位")], tools=[tool_schema])
    assert message.tool_calls[0]["name"] == "water.query_level"
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest backend/tests/runtime/test_gateway_model.py backend/tests/runtime/test_runner_gateway_api.py -k model -v`

Expected: FAIL because endpoint and adapter are absent.

- [ ] **Step 3: Implement normalized contracts and audit behavior**

`ModelInvocationRequest` contains messages, tool definitions, temperature, max output tokens and invocation sequence, but not provider secrets. The service obtains provider/model from the stored snapshot, clamps values to snapshot limits, invokes the existing `ModelGateway`, records the same `llm.invoke.succeeded/failed` audit fields used by `PlatformAgentHarness`, and stores the response under the idempotency key.

- [ ] **Step 4: Implement LangChain-compatible model adapter**

Subclass `BaseChatModel`; implement `_generate` to translate LangChain messages to the normalized gateway request and translate content, token usage and tool calls back to `ChatResult`. Map safe gateway codes to `RunnerGatewayModelError` without including response bodies or Authorization headers.

- [ ] **Step 5: Run model, Harness and audit regression tests**

Run: `python -m pytest backend/tests/runtime/test_gateway_model.py backend/tests/runtime/test_model_gateway.py backend/tests/runtime/test_harness.py backend/tests/audit -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/runtime/gateway_model.py backend/tests/runtime/test_gateway_model.py backend/app/runtime/runner_gateway_schemas.py backend/app/runtime/runner_gateway_service.py backend/app/runtime/runner_gateway_router.py backend/tests/runtime/test_runner_gateway_api.py
git commit -m "feat: mediate runner model invocations"
```

### Task 6: Tool, Skill, MCP And Knowledge Gateway Adapter

**Files:**
- Create: `backend/app/runtime/gateway_tools.py`
- Create: `backend/tests/runtime/test_gateway_tools.py`
- Modify: `backend/app/runtime/runner_gateway_schemas.py`
- Modify: `backend/app/runtime/runner_gateway_service.py`
- Modify: `backend/app/runtime/runner_gateway_router.py`
- Modify: `backend/tests/runtime/test_runner_gateway_api.py`

**Interfaces:**
- Consumes: `ToolGateway.execute(call, context, authorized_tool_ids) -> ToolExecutionResult`, snapshot tool ID/version/schema whitelist.
- Produces: `POST .../tool-invocations -> ToolInvocationResponse`, `build_gateway_tools(snapshot, client) -> list[BaseTool]`.

- [ ] **Step 1: Write failing current-state authorization tests**

```python
def test_tool_must_be_in_snapshot_and_currently_enabled(client, token, tool_store):
    assert invoke_tool(client, token, "not-snapshotted").json()["code"] == "tool_not_authorized"
    tool_store.disable("snapshotted-tool")
    response = invoke_tool(client, token, "snapshotted-tool")
    assert response.status_code == 403
    assert response.json()["code"] == "tool_not_authorized"

def test_approval_required_is_returned_as_interruption(client, token):
    response = invoke_tool(client, token, "reservoir.release")
    assert response.status_code == 409
    assert response.json()["code"] == "tool_approval_required"
    assert response.json()["approval_id"]
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest backend/tests/runtime/test_gateway_tools.py backend/tests/runtime/test_runner_gateway_api.py -k tool -v`

Expected: FAIL because the gateway tool endpoint is absent.

- [ ] **Step 3: Implement platform-side authorization and execution**

Resolve `ToolExecutionContext` exclusively from `ConversationRepository.get_run_execution_context`; compare requested tool ID and version to the immutable snapshot; then call the current `ToolGateway` with the snapshot whitelist. Do not special-case MCP or knowledge calls in Runner: their registered tools execute through the existing platform adapters and secrets remain in the API process.

- [ ] **Step 4: Implement DeepAgents/LangChain tool adapters**

```python
def build_gateway_tools(snapshot: ExecutionSnapshotPayload, client: RunnerGatewayClient) -> list[BaseTool]:
    return [
        StructuredTool.from_function(
            coroutine=partial(client.invoke_tool, tool_id=tool.id, version=tool.version),
            name=tool.name,
            description=tool.description,
            args_schema=json_schema_to_pydantic(tool.input_schema),
        )
        for tool in snapshot.actor.tools
        if tool.published and tool.enabled
    ]
```

Use the model-issued tool-call ID plus invocation sequence to create the idempotency key. Convert `tool_approval_required` into a typed `RunnerApprovalInterruption` carrying only approval ID and safe message.

- [ ] **Step 5: Run tool, MCP, approval and permission regressions**

Run: `python -m pytest backend/tests/runtime/test_gateway_tools.py backend/tests/tools backend/tests/test_mcp.py backend/tests/approvals -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/runtime/gateway_tools.py backend/tests/runtime/test_gateway_tools.py backend/app/runtime/runner_gateway_schemas.py backend/app/runtime/runner_gateway_service.py backend/app/runtime/runner_gateway_router.py backend/tests/runtime/test_runner_gateway_api.py
git commit -m "feat: mediate runner tool invocations"
```

### Task 7: Virtual Artifact Backend

**Files:**
- Create: `backend/app/runtime/artifact_backend.py`
- Create: `backend/tests/runtime/test_artifact_backend.py`
- Modify: `backend/app/artifacts/service.py`
- Modify: `backend/app/runtime/runner_gateway_schemas.py`
- Modify: `backend/app/runtime/runner_gateway_service.py`
- Modify: `backend/app/runtime/runner_gateway_router.py`
- Modify: `backend/tests/artifacts/test_service.py`

**Interfaces:**
- Consumes: `ArtifactService.create(..., run_id=run_id) -> ArtifactRecord`.
- Produces: `POST .../artifacts`, `GET .../artifacts`, `GET .../artifacts/{artifact_id}`, `ArtifactBackend.write(path, data, content_type)`, `.read(path)`, `.list(path)`.

- [ ] **Step 1: Write failing path, scope and size tests**

```python
@pytest.mark.parametrize("path", ["../secret", "C:/secret", "\\\\host\\share", "/artifacts/a\x00.txt"])
def test_artifact_backend_rejects_unsafe_paths(backend, path):
    with pytest.raises(ArtifactPathError):
        backend.write(path, b"data", "text/plain")

def test_runner_cannot_read_other_run_artifact(client, run_1_token, run_2_artifact):
    response = client.get(f"/internal/runner/runs/run-1/artifacts/{run_2_artifact.id}", headers=bearer(run_1_token))
    assert response.status_code == 404
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest backend/tests/runtime/test_artifact_backend.py backend/tests/artifacts/test_service.py -v`

Expected: FAIL because virtual Artifact routes and Run-scoped reads are absent.

- [ ] **Step 3: Add Run-scoped Artifact service methods**

Add `create_for_run`, `get_for_run`, and `list_for_run`, deriving unit/project/owner from the Run context. Use object keys `units/{unit_id}/projects/{project_id}/runs/{run_id}/{artifact_id}/{safe_name}`. Enforce `IAP_RUNNER_ARTIFACT_MAX_BYTES`, default `10485760`, accepted content types, SHA-256 validation and create-only semantics.

- [ ] **Step 4: Implement the virtual backend and events**

Normalize only paths under `/artifacts/`, upload bytes through the gateway, and return `ArtifactFile(path, artifact_id, size_bytes, sha256, content_type)`. The gateway creates the MinIO object and database row, then appends `artifact.ready` in the same request flow; failures remove a newly uploaded object before returning `artifact_upload_failed`.

- [ ] **Step 5: Run Artifact storage, API and backend regressions**

Run: `python -m pytest backend/tests/runtime/test_artifact_backend.py backend/tests/artifacts -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/runtime/artifact_backend.py backend/tests/runtime/test_artifact_backend.py backend/app/artifacts/service.py backend/app/runtime/runner_gateway_schemas.py backend/app/runtime/runner_gateway_service.py backend/app/runtime/runner_gateway_router.py backend/tests/artifacts/test_service.py
git commit -m "feat: add run scoped artifact backend"
```

### Task 8: Runner Gateway HTTP Client

**Files:**
- Create: `backend/app/runtime/runner_gateway_client.py`
- Create: `backend/tests/runtime/test_runner_gateway_client.py`
- Modify: `backend/app/runtime/execution_contract.py`
- Modify: `backend/tests/runtime/test_execution_contract.py`

**Interfaces:**
- Produces: `RunnerGatewayClient.from_execution_request(request)`, snapshot/model/tool/checkpoint/event/Artifact/completion methods; extended `RunExecutionRequest` fields `snapshot_id`, `snapshot_digest`, `gateway_url`, `run_token`.

- [ ] **Step 1: Write failing envelope and HTTP behavior tests**

```python
def test_execution_request_requires_gateway_identity():
    request = RunExecutionRequest.model_validate(valid_payload)
    assert request.snapshot_digest == "a" * 64
    assert request.gateway_url == "http://api:8000/internal/runner"

def test_client_sends_token_without_exposing_it_in_errors(fake_transport):
    client = RunnerGatewayClient("http://api:8000/internal/runner", "run-1", "secret-token", fake_transport)
    fake_transport.raise_timeout()
    with pytest.raises(RunnerGatewayUnavailable) as captured:
        client.get_snapshot()
    assert "secret-token" not in str(captured.value)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest backend/tests/runtime/test_runner_gateway_client.py backend/tests/runtime/test_execution_contract.py -v`

Expected: FAIL because the new envelope fields and client are absent.

- [ ] **Step 3: Extend the execution envelope**

Add validated `snapshot_id`, 64-character lowercase hexadecimal `snapshot_digest`, HTTP(S) `gateway_url`, and non-empty `run_token`. Mark `run_token` with `repr=False`; never include `RunExecutionRequest.model_dump()` in logs. Keep `deadline_at`, `agent_version` and `checkpoint_key` for lifecycle compatibility.

- [ ] **Step 4: Implement the client**

Use `httpx.Client` with connect timeout `3s`, read timeout bounded by the Run deadline, `Authorization: Bearer ...`, `X-Run-Id`, `X-Snapshot-Digest`, and `Idempotency-Key` where required. Parse only typed response contracts, cap response bytes, and map connectivity to `runner_gateway_unavailable` and business errors to typed safe exceptions.

- [ ] **Step 5: Run client and execution-contract tests**

Run: `python -m pytest backend/tests/runtime/test_runner_gateway_client.py backend/tests/runtime/test_execution_contract.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/runtime/runner_gateway_client.py backend/tests/runtime/test_runner_gateway_client.py backend/app/runtime/execution_contract.py backend/tests/runtime/test_execution_contract.py
git commit -m "feat: add runner gateway client"
```

### Task 9: Production SandboxRuntime With DeepAgents And LangGraph

**Files:**
- Create: `backend/app/runtime/sandbox_runtime.py`
- Create: `backend/tests/runtime/test_sandbox_runtime.py`
- Modify: `backend/app/runtime/deepagents_factory.py`
- Modify: `backend/app/runtime/langgraph_runtime.py`
- Modify: `backend/app/runtime/run_worker.py`
- Modify: `backend/Dockerfile.runner`
- Modify: `backend/tests/runtime/test_runner_image.py`

**Interfaces:**
- Consumes: `RunnerGatewayClient`, `GatewayChatModel`, `build_gateway_tools`, `ArtifactBackend`, `DeepAgentFactory.build`, `LangGraphRuntimeAdapter.invoke`.
- Produces: `SandboxRuntime.execute(request: RunExecutionRequest) -> RunExecutionResult`; `run_worker.main()` executes it and returns sanitized exit codes.

- [ ] **Step 1: Write failing end-to-end runtime unit tests with fake gateway**

```python
def test_runtime_builds_agent_restores_checkpoint_streams_events_and_completes(runtime, gateway):
    result = runtime.execute(execution_request)
    assert gateway.snapshot_reads == 1
    assert gateway.checkpoint_reads == 1
    assert gateway.events[0].event_type == "runner.started"
    assert gateway.completions[0].status == "completed"
    assert result.status == "completed"

def test_digest_mismatch_stops_before_model_or_tool_call(runtime, gateway):
    gateway.snapshot.digest = "b" * 64
    result = runtime.execute(execution_request)
    assert result.error_code == "snapshot_invalid"
    assert gateway.model_calls == []
    assert gateway.tool_calls == []
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest backend/tests/runtime/test_sandbox_runtime.py backend/tests/runtime/test_runner_image.py -v`

Expected: FAIL because `SandboxRuntime` is absent and `run_worker.py` only validates input.

- [ ] **Step 3: Implement runtime construction and bounded execution**

`SandboxRuntime` validates envelope deadline and snapshot digest, restores the latest matching checkpoint, creates gateway model/tools/Artifact backend, calls `DeepAgentFactory`, wraps the result in `LangGraphRuntimeAdapter`, invokes with `thread_id=run_id`, emits ordered events, saves checkpoints and sends one idempotent completion. Apply snapshot maximum iterations/tool calls/subagents/output bytes. Skill instructions are prompt/context only; executable actions remain gateway tools.

- [ ] **Step 4: Implement interruption and safe failure mapping**

On `RunnerApprovalInterruption`, save checkpoint key `approval-{approval_id}`, append `approval.required`, and complete with non-terminal interruption metadata rather than marking success. Map all other typed errors to the stable codes in the design; raw exceptions become `sandbox_failed` and are never returned verbatim.

- [ ] **Step 5: Replace the worker stub and validate the image boundary**

`run_worker.main()` must load the envelope, construct the client/runtime, execute, and return `0` only for completed or accepted interruption results; use fixed nonzero codes for failed, cancelled and invalid envelope outcomes. Assert the Runner image has DeepAgents/LangGraph dependencies but no API server startup, migration command, Docker CLI or secret environment declarations.

- [ ] **Step 6: Run all runtime adapter tests**

Run: `python -m pytest backend/tests/runtime/test_sandbox_runtime.py backend/tests/runtime/test_deepagents_factory.py backend/tests/runtime/test_deepagent_node.py backend/tests/runtime/test_langgraph_runtime.py backend/tests/runtime/test_runner_image.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/runtime/sandbox_runtime.py backend/tests/runtime/test_sandbox_runtime.py backend/app/runtime/deepagents_factory.py backend/app/runtime/langgraph_runtime.py backend/app/runtime/run_worker.py backend/Dockerfile.runner backend/tests/runtime/test_runner_image.py
git commit -m "feat: execute deep agents in sandbox runner"
```

### Task 10: Coordinator Snapshot Issuance, Token Revocation And Completion Reconciliation

**Files:**
- Modify: `backend/app/runtime/run_lifecycle.py`
- Modify: `backend/app/runtime/workflow_runner.py`
- Modify: `backend/app/runtime/workflow_runner_api.py`
- Modify: `backend/app/runtime/launcher_client.py`
- Modify: `backend/app/runtime/container_launcher.py`
- Modify: `backend/app/runtime/container_policy.py`
- Modify: `backend/tests/runtime/test_run_lifecycle.py`
- Modify: `backend/tests/runtime/test_workflow_runner.py`
- Modify: `backend/tests/runtime/test_workflow_runner_api.py`
- Modify: `backend/tests/runtime/test_container_launcher.py`

**Interfaces:**
- Consumes: snapshot and token services; extended execution envelope.
- Produces: coordinator lifecycle that creates snapshot/token before submit, revokes on every terminal path, and reconciles gateway completion with container exit idempotently.

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_coordinator_issues_snapshot_and_token_before_submit(coordinator, runner, snapshots, tokens):
    coordinator.execute("run-1")
    assert runner.submissions[0]["snapshot_id"] == snapshots.created[0].snapshot_id
    assert runner.submissions[0]["run_token"] == tokens.issued[0].value

@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled", "sandbox_timeout", "sandbox_oom"])
def test_every_terminal_path_revokes_token(coordinator, terminal, tokens):
    drive_to_terminal(coordinator, terminal)
    assert tokens.revoked == [("run-1", terminal)]
```

- [ ] **Step 2: Run lifecycle tests and verify failure**

Run: `python -m pytest backend/tests/runtime/test_run_lifecycle.py backend/tests/runtime/test_workflow_runner.py backend/tests/runtime/test_container_launcher.py -v`

Expected: FAIL because submissions carry only Agent/checkpoint metadata.

- [ ] **Step 3: Create snapshot and token atomically before submission**

After `_start`, create or retrieve the Run snapshot, issue the allowed action set, and submit `snapshot_id`, digest, gateway URL, token and deadline. Repeated coordinator execution must reuse the immutable snapshot and reject a conflicting digest.

- [ ] **Step 4: Extend Workflow Runner and Launcher envelopes without persisting tokens**

Pass the token only in the Docker environment value `IAP_RUN_EXECUTION_REQUEST`; never return it from status endpoints or store it in Launcher maps, Run events or audits. Container inspect responses remain limited to Run ID, container ID, status, exit code and OOM flag.

- [ ] **Step 5: Reconcile terminal state idempotently**

If Runner Gateway has already committed completion, container exit `0` must not add a conflicting completion. If the container exits without completion, coordinator applies the existing safe sandbox failure mapping. Cancellation, timeout, OOM, Launcher outage, administrative termination and normal completion revoke the Run token before cleanup.

- [ ] **Step 6: Run lifecycle, cancellation and cleanup regressions**

Run: `python -m pytest backend/tests/runtime/test_run_lifecycle.py backend/tests/runtime/test_workflow_runner.py backend/tests/runtime/test_workflow_runner_api.py backend/tests/runtime/test_container_launcher.py backend/tests/runtime/test_launcher_client.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/runtime/run_lifecycle.py backend/app/runtime/workflow_runner.py backend/app/runtime/workflow_runner_api.py backend/app/runtime/launcher_client.py backend/app/runtime/container_launcher.py backend/app/runtime/container_policy.py backend/tests/runtime/test_run_lifecycle.py backend/tests/runtime/test_workflow_runner.py backend/tests/runtime/test_workflow_runner_api.py backend/tests/runtime/test_container_launcher.py
git commit -m "feat: coordinate scoped sandbox executions"
```

### Task 11: Dedicated Internal Network And Secret Boundary

**Files:**
- Modify: `compose.yaml`
- Modify: `backend/app/runtime/container_policy.py`
- Modify: `backend/app/runtime/sandbox_inspector.py`
- Modify: `backend/app/runtime/sandbox_readiness.py`
- Modify: `backend/tests/runtime/test_container_policy.py`
- Modify: `backend/tests/runtime/test_sandbox_inspector.py`
- Modify: `backend/tests/runtime/test_launcher_compose_boundary.py`
- Create: `backend/tests/integration/test_runner_secret_boundary.py`
- Modify: `docs/deployment/sandbox-staging-acceptance.md`

**Interfaces:**
- Produces: internal `runner-gateway` Docker network connecting only API, Workflow Runner/Launcher-managed Run containers; container policy permitting gateway traffic without general outbound access.

- [ ] **Step 1: Write failing network and secret-boundary tests**

```python
def test_run_container_joins_only_runner_gateway_network(policy):
    config = policy.build("run-1", "/workspace/run-1", execution_request="{}")
    assert config["network"] == "intelligent-agent-platform_runner-gateway"

def test_runner_environment_contains_no_platform_secrets(inspected_environment):
    forbidden = {"DATABASE_URL", "IAP_OBJECT_STORAGE_SECRET_KEY", "OPENAI_API_KEY", "MCP_TOKEN", "DOCKER_HOST"}
    assert forbidden.isdisjoint(inspected_environment)
```

- [ ] **Step 2: Run boundary tests and verify failure**

Run: `python -m pytest backend/tests/runtime/test_container_policy.py backend/tests/runtime/test_launcher_compose_boundary.py backend/tests/integration/test_runner_secret_boundary.py -v`

Expected: FAIL because current production-ready staging uses `network=none` and no gateway network exists.

- [ ] **Step 3: Add the internal network and fixed gateway identity**

Declare an `internal: true` Compose network named `runner-gateway`; attach `api`, `workflow-runner` and `sandbox-launcher`. The Launcher connects Run containers to the exact configured network, injects `IAP_RUNNER_GATEWAY_URL=http://api:8000/internal/runner`, retains read-only root, non-root user, dropped capabilities, PID/memory/CPU limits and no host mounts except the approved workspace policy.

- [ ] **Step 4: Tighten readiness inspection**

Readiness requires the exact internal network, trusted image, non-root user, read-only root, resource limits, dropped capabilities, no Docker Socket and an environment allowlist containing only execution request and non-secret runtime settings. Sanitize all inspection failures.

- [ ] **Step 5: Document deployment injection and rollback**

Document that `IAP_RUNNER_TOKEN_SIGNING_KEY` and Launcher service token are injected by the deployment secret manager, not committed. Include startup, health, inspection and rollback commands; rollback sets sandbox false, revokes active Run tokens, terminates Run containers and removes the sandbox profile services.

- [ ] **Step 6: Run security boundary tests**

Run: `python -m pytest backend/tests/runtime/test_container_policy.py backend/tests/runtime/test_sandbox_inspector.py backend/tests/runtime/test_launcher_compose_boundary.py backend/tests/integration/test_runner_secret_boundary.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add compose.yaml backend/app/runtime/container_policy.py backend/app/runtime/sandbox_inspector.py backend/app/runtime/sandbox_readiness.py backend/tests/runtime/test_container_policy.py backend/tests/runtime/test_sandbox_inspector.py backend/tests/runtime/test_launcher_compose_boundary.py backend/tests/integration/test_runner_secret_boundary.py docs/deployment/sandbox-staging-acceptance.md
git commit -m "feat: isolate runner gateway network"
```

### Task 12: Full Integration, Exceptional Scenarios And Enablement Report

**Files:**
- Create: `backend/tests/integration/test_runner_gateway_execution.py`
- Create: `backend/tests/integration/test_runner_gateway_failures.py`
- Create: `backend/tests/integration/test_runner_gateway_artifacts.py`
- Create: `docs/deployment/runner-gateway-staging-results-2026-08-14.md`
- Modify: `docs/deployment/sandbox-staging-acceptance.md`

**Interfaces:**
- Consumes: the complete execution gateway path from coordinator to per-Run container and back.
- Produces: evidence for the six enablement gates; does not change the default feature flag.

- [ ] **Step 1: Add the normal-path integration test**

Create a Run with a published test Agent, fake provider and authorized deterministic tool. Assert status progression `pending -> running -> completed`, ordered intermediate events, model invocation audit, tool invocation record, final assistant message, checkpoint persistence, Artifact creation, downloadable MinIO bytes and history fields for Run time, Agent and status.

- [ ] **Step 2: Add exceptional-scenario integration tests**

Cover expired token, revoked token, snapshot digest mismatch, cross-Run token, duplicate event, duplicate completion, disabled tool, unauthorized tool, approval interruption/resume, model timeout, MCP offline, checkpoint failure, Artifact failure, cancellation, deadline, OOM and Launcher outage. Assert stable safe codes, `403` or `404` for unauthorized cross-scope access, no token/credential/internal-host-path leakage, final status persistence and idempotent cleanup.

- [ ] **Step 3: Run the complete automated suite**

Run: `python -m pytest backend/tests -v`

Expected: all tests PASS.

- [ ] **Step 4: Build and inspect deployment images**

Run: `docker compose build api workflow-runner sandbox-launcher`

Run: `docker compose --profile sandbox config`

Expected: configuration resolves; Run containers have only the internal gateway network and no forbidden secrets or mounts.

- [ ] **Step 5: Execute staging acceptance**

With deployment-owned tokens injected, run: normal task, authorized tool, unauthorized tool, generated file, intermediate process view, final download, history query, approval resume, cancellation, timeout, OOM and Launcher outage. After each terminal path assert `docker ps -a --filter name=iap-run-` is empty and Run/event/audit/Artifact records remain traceable.

- [ ] **Step 6: Record evidence and keep the default disabled**

Write exact test counts, image digests, inspected environment key names, scenario results, cleanup evidence, known residual risks, rollback evidence and deployment-owner approval status into `docs/deployment/runner-gateway-staging-results-2026-08-14.md`. Do not set `IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED` default to true in this task.

- [ ] **Step 7: Final verification**

Run: `git diff --check`

Run: `python -m pytest backend/tests -q`

Expected: no whitespace errors and the complete suite passes.

- [ ] **Step 8: Commit**

```bash
git add backend/tests/integration/test_runner_gateway_execution.py backend/tests/integration/test_runner_gateway_failures.py backend/tests/integration/test_runner_gateway_artifacts.py docs/deployment/runner-gateway-staging-results-2026-08-14.md docs/deployment/sandbox-staging-acceptance.md
git commit -m "test: accept runner execution gateway"
```

## Enablement Decision

After Task 12, enable production sandbox execution only in a separate deployment change when all of these are evidenced in the staging report:

1. Real immutable snapshots execute through DeepAgents and LangGraph inside per-Run containers.
2. Model, Skill, MCP and knowledge calls use platform gateways.
3. Checkpoint, event, completion and MinIO Artifact integration tests pass.
4. Runner image/container inspection proves the absence of database, storage, provider, MCP and Docker credentials.
5. Normal and exceptional staging acceptance passes with no residual `iap-run-*` containers.
6. The deployment owner approves the service identity, secret injection, network policy and rollback procedure.

