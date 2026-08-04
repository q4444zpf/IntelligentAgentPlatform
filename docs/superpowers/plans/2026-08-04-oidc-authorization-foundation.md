# OIDC and Local Authorization Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first production-oriented authentication and authorization vertical slice: PostgreSQL identity and grant data, OIDC BFF login, opaque Cookie sessions, CSRF protection, explicit project selection, backend authentication gates, and a Vue session state machine tested against a real protocol-level Mock OIDC Provider.

**Architecture:** Keep the current FastAPI synchronous SQLAlchemy architecture and add one focused `identity` package for OIDC, sessions, authorization, bootstrap, and auth APIs. PostgreSQL remains the only session and authorization store; the browser receives only opaque Cookies and a memory-only CSRF Token. The existing Vue visual shell remains intact while its Mock Token, role switch, development headers, and static permission source are replaced by `/api/auth/me`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16, HTTPX, Authlib JOSE/OIDC primitives, cryptography AES-GCM, pytest, Vue 3, TypeScript 5, Pinia, Vue Router, Vitest, Docker Compose

## Global Constraints

- The authoritative specification is `docs/superpowers/specs/2026-08-04-oidc-local-authorization-design.md`.
- Web authentication uses OIDC Authorization Code + PKCE; external Claims authenticate identity but never grant platform permissions.
- The browser must never receive or persist OIDC Access, Refresh, or ID Tokens.
- The production session Cookie is `__Host-iap_session` with `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, and no `Domain`.
- The path-limited login transaction Cookie is `__Secure-iap_oidc_tx` with `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/api/auth/callback`, and no `Domain`.
- PostgreSQL is the sole source for users, units, projects, memberships, roles, permission-and-scope tuples, login transactions, and sessions; do not add Redis.
- The first deployment has one active unit, multiple projects, multiple roles per user, and `current_project_id: str | None`.
- Effective authorization is the union of indivisible `(permission_code, scope_predicate)` grants. Never merge permission codes and scopes independently.
- Cookie-session `POST`, `PUT`, `PATCH`, and `DELETE` requests require the CSRF Token, exact configured Origin, and fail-closed Fetch Metadata validation; only the exact OIDC back-channel and pre-session emergency login contracts defined by their dedicated plan are exempt.
- Development identity headers are permitted only in explicit `development` or `test` mode from loopback. Production startup fails when the adapter or frontend `VITE_DEV_*` identity is enabled.
- No production HTTP deployment is accepted. OIDC production validation requires a public HTTPS origin.
- All secrets support environment-variable or `_FILE` injection and are redacted from logs, exceptions, audit metadata, and API responses.
- Use 4-space Python indentation and 2-space TypeScript, JSON, and YAML indentation.
- Every behavior change starts with a failing test and ends with a focused passing test and a conventional commit.
- Every command block is standalone and runs from the repository root containing `backend/`, `frontend/`, and `compose.yaml`; no block depends on a prior `cd` or environment mutation.
- Every commit stages only the explicit file paths in that task, then runs `git diff --cached --name-only`; abort unless the output exactly equals that task File list and excludes `.task5-harness-root.py`.
- `.task5-harness-root.py` is an external local harness; do not read, modify, stage, delete, or commit it.

## Route And Menu Contract

Backend menus and the frontend registry share this closed allowlist. The implementation may reorganize the tree, but it must not rename a key, accept a server-supplied component path, or reintroduce `tenant` terminology. A visibility requirement is the pair `(permission_code, target_kind)`: `unit` requires a grant that covers `ResourceScope(context.unit_id, None, None)`; `current_project` requires a selected project and a grant whose own predicate covers that project. A matching code with the wrong target scope does not expose the menu.

`GET /api/auth/me.permissions` returns the same entry-effective pairs as `{code, target}`, deduplicated and sorted. It never returns bare permission strings from which the frontend would have to infer scope. A unit grant may produce both target pairs after a project is selected; when `current_project=null`, only unit pairs are returned.

| `route_key` | Path | Frontend component | Visibility permission | Target |
| --- | --- | --- | --- | --- |
| `dashboard` | `/dashboard` | `views/dashboard/DashboardView.vue` | `platform.read` | `unit` |
| `chat` | `/chat` | `views/agent/AgentConsoleView.vue` | `agent.run` | `unit` |
| `agent-manage` | `/agent/manage` | `views/agent/AgentManageView.vue` | `agent.manage` | `unit` |
| `collaboration` | `/collaboration` | `views/platform/GenericModuleView.vue` | `collaboration.read` | `current_project` |
| `workflow` | `/workflow` | `views/platform/GenericModuleView.vue` | `workflow.read` | `current_project` |
| `llm` | `/llm` | `views/settings/ModelProviderView.vue` | `model.manage` | `unit` |
| `mcp` | `/mcp` | `views/mcp/McpManageView.vue` | `mcp.manage` | `unit` |
| `skill` | `/skill` | `views/skills/SkillManageView.vue` | `skill.manage` | `unit` |
| `tools` | `/tools` | `views/tools/ToolManageView.vue` | `tool.manage` | `unit` |
| `knowledge` | `/knowledge` | `views/platform/GenericModuleView.vue` | `knowledge.read` | `current_project` |
| `prompt` | `/prompt` | `views/platform/GenericModuleView.vue` | `prompt.read` | `current_project` |
| `external-agents` | `/external-agents` | `views/platform/GenericModuleView.vue` | `integration.read` | `unit` |
| `my-agents` | `/personal/agents` | `views/resources/ResourceListView.vue` | `resource.read` | `current_project` |
| `my-mcp` | `/personal/mcp` | `views/resources/ResourceListView.vue` | `resource.read` | `current_project` |
| `my-skills` | `/personal/skills` | `views/resources/ResourceListView.vue` | `resource.read` | `current_project` |
| `my-publish` | `/personal/publish` | `views/resources/ResourceListView.vue` | `resource.publish` | `current_project` |
| `project-resources` | `/project/resources` | `views/resources/ResourceListView.vue` | `resource.read` | `current_project` |
| `hydraulic-topology` | `/resources/topology` | `views/resources/TopologyDataView.vue` | `resource.read` | `current_project` |
| `unit-resources` | `/unit/resources` | `views/resources/ResourceListView.vue` | `resource.read` | `unit` |
| `public-agents` | `/public/agents` | `views/resources/ResourceListView.vue` | `resource.read` | `current_project` |
| `public-mcp` | `/public/mcp` | `views/resources/ResourceListView.vue` | `resource.read` | `current_project` |
| `public-skills` | `/public/skills` | `views/resources/ResourceListView.vue` | `resource.read` | `current_project` |
| `publish-review` | `/public/review` | `views/resources/ResourceListView.vue` | `resource.review` | `current_project` |
| `runs` | `/runs` | `views/runs/AgentRunListView.vue` | `conversation.read` | `current_project` |
| `async-tasks` | `/async-tasks` | `views/platform/GenericModuleView.vue` | `workflow.read` | `current_project` |
| `sandbox` | `/system/sandbox` | `views/security/SandboxMonitorView.vue` | `sandbox.read` | `unit` |
| `artifacts` | `/artifacts` | `views/platform/GenericModuleView.vue` | `artifact.read` | `current_project` |
| `approvals` | `/approvals` | `views/resources/ResourceListView.vue` | `approval.read` | `current_project` |
| `policies` | `/policies` | `views/platform/GenericModuleView.vue` | `policy.read` | `unit` |
| `credentials` | `/credentials` | `views/platform/GenericModuleView.vue` | `credential.read` | `unit` |
| `audit` | `/system/audit` | `views/security/AuditLogView.vue` | `audit.read` | `unit` |
| `users` | `/system/users` | `views/platform/GenericModuleView.vue` | `identity.read` | `unit` |
| `unit-projects` | `/system/unit-projects` | `views/platform/GenericModuleView.vue` | `project.read` | `unit` |
| `roles` | `/system/roles` | `views/platform/GenericModuleView.vue` | `identity.read` | `unit` |
| `integration` | `/integration` | `views/platform/GenericModuleView.vue` | `integration.read` | `unit` |
| `settings` | `/system/settings` | `views/platform/GenericModuleView.vue` | `settings.read` | `unit` |

`chat` is the only phase-one composite navigation requirement: its stored `visibility_target` and required capability remain `unit`, but it also requires `current_project_id` to be non-null. The backend omits this menu leaf when no project is selected, and the frontend registry marks both `/chat` and `/chat/focus` as project-required before mounting `AgentConsoleView`; a project-target `agent.run` never satisfies the unit capability.

The six stable group keys are `agents`, `capabilities`, `resources`, `operations`, `security`, and `system`. A group is not a route, has `route_key=null`, `visibility_target=null`, and `requires_current_project=false`, and is visible only when at least one child survives authorization filtering. A route node has one allowlisted `route_key`. Persist `kind`, stable `node_key`, nullable `route_key`, parent, label, order, status, visibility target, and `requires_current_project`; CHECK constraints enforce the closed project-requirement catalogue defined above.

These routes are compiled into the frontend and never created or removed from a server menu:

| Fixed path | Component/behavior | Access contract |
| --- | --- | --- |
| `/` | `AppLayout.vue`, dynamic-route host | Authenticated; enter the first authorized unit route, otherwise the first authorized project route when a project is selected; with no project selected use `/select-project` when `projects` is non-empty, and use `/403` when no destination exists |
| `/login` | `views/auth/LoginView.vue` | Public; authenticated users leave it |
| `/select-project` | `views/auth/ProjectSelectionView.vue` | Authenticated; explicitly permits `current_project=null` |
| `/403` | `views/errors/ForbiddenView.vue` | Authenticated; keeps the session and does not depend on a menu |
| `/404` | `views/errors/NotFoundView.vue` | Fixed error route; keeps an existing session |
| `/chat/focus` | `views/agent/AgentConsoleView.vue` | Authenticated, selected project, and unit-target `agent.run`; not dependent on a menu row |
| `/tenant/resources` | Redirect to `/unit/resources` | Authenticated compatibility alias for one release; missing/unauthorized dynamic target goes to `/403` |
| `/system/tenant-projects` | Redirect to `/system/unit-projects` | Authenticated compatibility alias for one release; missing/unauthorized dynamic target goes to `/403` |
| `/:pathMatch(.*)*` | Redirect to `/404` | Must not silently redirect to the dashboard |

An unknown group key, route key, duplicate path, or attempt to override a fixed route rejects the complete menu response. The frontend removes all dynamic routes, preserves the authenticated session, and shows `/403` with `AUTH_MENU_CONFIGURATION_INVALID`; it never installs a partial tree.

---

## Scope And Release Gates

This plan is the first independently testable phase. It ends with a browser login shell that can authenticate through the protocol-level Mock OIDC Provider, restore a server-side session, select a project, and receive database-derived permissions and menus.

Production release remains blocked until these named follow-up plans are complete:

1. `business-resource-authorization` adds unit/project/owner fields or explicit grant tables to Agent, MCP, Tool, model, knowledge, workflow, Artifact, and Run resources and enforces object-level SQL predicates.
2. `authorization-administration` adds user binding, project membership, role, permission, and menu management APIs and their existing-style Web administration pages.
3. `emergency-and-provider-hardening` adds the isolated emergency administrator, applies this phase's verified client-address resolver to the emergency CIDR policy, Back-Channel Logout, RP-Initiated Logout adaptation, Keycloak browser E2E, and real-Provider acceptance.
4. `authentication-release-acceptance` runs full PostgreSQL, Mock OIDC, standard/real Provider, browser, security-header, secret-redaction, downgrade, and rollback verification.

Because Agent records are still global in this phase, Chat execution is a deliberate temporary restriction: both conversation creation and message submission require a selected project plus unit-target `agent.run`. Project-only `agent.run` cannot invoke any global Agent, even with a guessed ID. The Chat menu and fixed focus route use the same unit-target capability until gate 1 introduces a project-filtered executable-Agent catalogue.

Until those gates close, production startup documentation must state that authenticated business APIs are not approved for customer data.

## File Map

**Create:**

- `backend/app/identity/__init__.py`
- `backend/app/identity/models.py`
- `backend/app/identity/schemas.py`
- `backend/app/identity/crypto.py`
- `backend/app/identity/client_address.py`
- `backend/app/identity/repository.py`
- `backend/app/identity/catalogue.py`
- `backend/app/identity/bootstrap.py`
- `backend/app/identity/authorization.py`
- `backend/app/identity/sessions.py`
- `backend/app/identity/csrf.py`
- `backend/app/identity/oidc.py`
- `backend/app/identity/dependencies.py`
- `backend/app/identity/middleware.py`
- `backend/app/identity/router.py`
- `backend/alembic/versions/20260804_09_identity_authorization_foundation.py`
- `backend/alembic/versions/20260804_10_audit_auth_contract.py`
- `backend/tests/conftest.py`
- `backend/tests/identity/__init__.py`
- `backend/tests/identity/test_crypto.py`
- `backend/tests/identity/test_client_address.py`
- `backend/tests/identity/test_models.py`
- `backend/tests/identity/test_bootstrap.py`
- `backend/tests/identity/test_authorization.py`
- `backend/tests/identity/test_sessions.py`
- `backend/tests/identity/test_csrf.py`
- `backend/tests/identity/test_oidc_client.py`
- `backend/tests/identity/test_auth_api.py`
- `backend/tests/identity/test_project_context.py`
- `backend/tests/support/mock_oidc_provider.py`
- `backend/tests/support/run_postgres_tests.ps1`
- `backend/tests/integration/test_identity_migrations.py`
- `backend/tests/integration/test_oidc_mock_flow.py`
- `backend/tests/security/test_deployment_security.py`
- `frontend/src/api/auth.ts`
- `frontend/src/api/auth.test.ts`
- `frontend/src/stores/auth.ts`
- `frontend/src/stores/auth.test.ts`
- `frontend/src/router/routeRegistry.ts`
- `frontend/src/router/dynamicRoutes.ts`
- `frontend/src/router/dynamicRoutes.test.ts`
- `frontend/src/router/index.test.ts`
- `frontend/src/views/auth/LoginView.test.ts`
- `frontend/src/views/auth/ProjectSelectionView.vue`
- `frontend/src/views/auth/ProjectSelectionView.test.ts`
- `frontend/src/views/resources/ResourceListView.test.ts`
- `frontend/src/views/errors/ForbiddenView.vue`
- `frontend/src/views/errors/NotFoundView.vue`
- `frontend/src/layouts/AppLayout.test.ts`
- `docs/deployment/oidc-development.md`

**Modify:**

- `backend/requirements.txt`
- `backend/app/core/settings.py`
- `backend/app/core/request_context.py`
- `backend/app/db/base.py`
- `backend/app/main.py`
- `backend/app/audit/models.py`
- `backend/app/audit/backfill.py`
- `backend/app/audit/management.py`
- `backend/app/audit/recorder.py`
- `backend/app/audit/schemas.py`
- `backend/app/audit/policy.py`
- `backend/app/audit/repository.py`
- `backend/app/audit/service.py`
- `backend/app/audit/router.py`
- `backend/app/conversations/models.py`
- `backend/app/conversations/repository.py`
- `backend/app/conversations/service.py`
- `backend/app/conversations/router.py`
- `backend/app/runtime/harness.py`
- `backend/app/agents/service.py`
- `backend/app/agents/router.py`
- `backend/app/mcp/service.py`
- `backend/app/mcp/router.py`
- `backend/app/tools/service.py`
- `backend/app/tools/gateway.py`
- `backend/app/tools/router.py`
- `backend/app/tools/schemas.py`
- `backend/app/model_providers/service.py`
- `backend/app/model_providers/router.py`
- `backend/app/skills/router.py`
- `backend/app/platform/router.py`
- `backend/tests/core/test_settings.py`
- `backend/tests/core/test_request_context.py`
- `backend/tests/test_main.py`
- `backend/tests/test_agents.py`
- `backend/tests/test_mcp.py`
- `backend/tests/test_model_providers.py`
- `backend/tests/test_skills.py`
- `backend/tests/test_platform.py`
- `backend/tests/tools/test_api.py`
- `backend/tests/tools/test_gateway.py`
- `backend/tests/conversations/test_api.py`
- `backend/tests/conversations/test_models.py`
- `backend/tests/conversations/test_repository.py`
- `backend/tests/conversations/test_service.py`
- `backend/tests/audit/test_api.py`
- `backend/tests/audit/test_backfill.py`
- `backend/tests/audit/test_models.py`
- `backend/tests/audit/test_recorder.py`
- `backend/tests/audit/test_repository.py`
- `backend/tests/audit/test_service.py`
- `backend/tests/runtime/test_harness.py`
- `frontend/src/main.ts`
- `frontend/src/api/client.ts`
- `frontend/src/api/client.test.ts`
- `frontend/src/api/runEvents.ts`
- `frontend/src/api/runEvents.test.ts`
- `frontend/src/api/audit.ts`
- `frontend/src/api/audit.test.ts`
- `frontend/src/router/index.ts`
- `frontend/src/router/routes.ts`
- `frontend/src/views/auth/LoginView.vue`
- `frontend/src/layouts/AppLayout.vue`
- `frontend/src/stores/conversations.ts`
- `frontend/src/stores/conversations.test.ts`
- `frontend/src/views/security/AuditLogView.vue`
- `frontend/src/views/security/AuditLogView.test.ts`
- `frontend/src/views/resources/ResourceListView.vue`
- `frontend/src/views/tools/ToolManageView.test.ts`
- `frontend/src/views/runs/AgentRunListView.test.ts`
- `frontend/src/vite-env.d.ts`
- `frontend/Dockerfile`
- `frontend/nginx.conf`
- `backend/Dockerfile`
- `.env.example`
- `compose.yaml`
- `README.md`
- `backend/README.md`
- `frontend/README.md`
- `docs/智能体平台详细功能设计与现状改造清单.md`
- `backend/tests/test_frontend_dev_identity_config.py`
- `docs/deployment/aliyun-ecs-http.md`
- `backend/tests/integration/test_audit_concurrency.py`
- `backend/tests/integration/test_postgres_migrations.py`

**Delete after all imports are migrated:**

- `frontend/src/stores/permission.ts`

### Task 1: Add Fail-Closed Auth Configuration And Cryptography

**Files:**

- Modify: `backend/requirements.txt`
- Modify: `backend/app/core/settings.py`
- Create: `backend/app/identity/crypto.py`
- Create: `backend/tests/identity/__init__.py`
- Create: `backend/tests/identity/test_crypto.py`
- Modify: `backend/tests/core/test_settings.py`

**Interfaces:**

- Produces `Settings.from_env()`, `Settings.validate_startup()`, `read_secret(name: str) -> str | None`, `EnvelopeCipher.encrypt(value: bytes) -> dict[str, str]`, `EnvelopeCipher.decrypt(value: Mapping[str, str]) -> bytes`, and `hash_opaque_token(raw: str, key: bytes) -> str`.
- Later tasks consume the parsed OIDC settings, current encryption key ID, previous decryption keys, session HMAC key, public origin, environment, and trusted proxy CIDRs.

- [ ] **Step 1: Write failing settings and crypto tests**

Add exact cases named:

```python
def test_production_rejects_development_identity(monkeypatch):
    monkeypatch.setenv("IAP_ENVIRONMENT", "production")
    monkeypatch.setenv("IAP_ALLOW_DEV_IDENTITY", "true")
    with pytest.raises(ValueError, match="development identity"):
        Settings.from_env().validate_startup()

def test_ciphertext_uses_current_key_and_old_key_still_decrypts():
    old = bytes(range(32))
    current = bytes(reversed(range(32)))
    cipher = EnvelopeCipher(current_key_id="k2", keys={"k1": old, "k2": current})
    encrypted = cipher.encrypt(b"csrf-secret")
    assert encrypted["kid"] == "k2"
    assert cipher.decrypt(encrypted) == b"csrf-secret"
```

Also test production rejects an HTTP public origin, a non-HTTPS issuer, missing client configuration, missing/short HMAC and AES keys, simultaneous `NAME` and `NAME_FILE`, unreadable secret files, unknown environments, insecure Cookies, and a Mock issuer. Test `repr(settings)` does not contain any secret value and tampered AES-GCM ciphertext fails authentication.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```powershell
$env:PYTHONPATH = "backend"
python -m pytest -q backend/tests/core/test_settings.py backend/tests/identity/test_crypto.py
```

Expected: FAIL because the auth settings and crypto module do not exist.

- [ ] **Step 3: Add dependencies and implement the configuration contract**

Add `authlib>=1.6.5,<2` and `cryptography>=45.0.0,<47` to `backend/requirements.txt`. Keep HTTPX, SQLAlchemy, and the current synchronous database stack.

Use these environment names:

```text
IAP_ENVIRONMENT
IAP_PUBLIC_BASE_URL
IAP_ALLOW_DEV_IDENTITY
IAP_SESSION_COOKIE_SECURE
IAP_SESSION_HMAC_KEY / IAP_SESSION_HMAC_KEY_FILE
IAP_AUTH_ENCRYPTION_KEYS / IAP_AUTH_ENCRYPTION_KEYS_FILE
OIDC_ISSUER
OIDC_CLIENT_ID
OIDC_CLIENT_SECRET / OIDC_CLIENT_SECRET_FILE
OIDC_REDIRECT_URI
OIDC_SCOPE
OIDC_CONNECT_TIMEOUT_SECONDS
OIDC_READ_TIMEOUT_SECONDS
OIDC_CLOCK_SKEW_SECONDS
TRUSTED_PROXY_CIDRS
```

`IAP_AUTH_ENCRYPTION_KEYS` is a comma-separated key ring in `kid:base64url-32-byte-key` form; the first key encrypts and every listed key may decrypt. Reject duplicate key IDs and malformed lengths.

Core helpers:

```python
def hash_opaque_token(raw: str, key: bytes) -> str:
    return hmac.new(key, raw.encode("utf-8"), hashlib.sha256).hexdigest()

class EnvelopeCipher:
    def encrypt(self, value: bytes) -> dict[str, str]:
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self.current_key).encrypt(nonce, value, self.current_key_id.encode())
        return {
            "kid": self.current_key_id,
            "nonce": base64.urlsafe_b64encode(nonce).decode().rstrip("="),
            "ciphertext": base64.urlsafe_b64encode(ciphertext).decode().rstrip("="),
        }
```

`validate_startup()` must reject production startup when development identity is enabled, the public origin or issuer is not HTTPS, secure Cookies are disabled, an auth key is absent, or the redirect origin differs from `IAP_PUBLIC_BASE_URL`.

- [ ] **Step 4: Run tests and commit**

Run:

```powershell
python -m pip install -r backend/requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Backend dependency installation failed" }
$env:PYTHONPATH = "backend"
python -m pytest -q backend/tests/core/test_settings.py backend/tests/identity/test_crypto.py
```

Expected: PASS.

```powershell
git add -- backend/requirements.txt backend/app/core/settings.py backend/app/identity/crypto.py backend/tests/identity/__init__.py backend/tests/identity/test_crypto.py backend/tests/core/test_settings.py
git diff --cached --name-only
git commit -m "feat: add fail-closed auth configuration"
```

Expected before commit: the staged-name output is exactly the six Task 1 files above.

### Task 2: Create The Identity And Authorization Schema

**Files:**

- Create: `backend/app/identity/__init__.py`
- Create: `backend/app/identity/models.py`
- Create: `backend/alembic/versions/20260804_09_identity_authorization_foundation.py`
- Modify: `backend/app/db/base.py`
- Create: `backend/tests/identity/test_models.py`
- Create: `backend/tests/integration/test_identity_migrations.py`
- Create: `backend/tests/support/run_postgres_tests.ps1`
- Modify: `backend/tests/integration/test_postgres_migrations.py`

**Interfaces:**

- Produces SQLAlchemy models for users, external identities/history, units, projects, memberships, roles, permissions, permission-and-scope tuples, role bindings, menu mappings, OIDC transactions, and auth sessions.
- Task 3 writes audit snapshots against these identities; Tasks 4-8 use these tables without adding parallel in-memory authorization state.

- [ ] **Step 1: Write failing model and PostgreSQL constraint tests**

Model tests assert table names, nullable fields, unique constraints, and `AuthSession.current_project_id is None`. PostgreSQL tests create two units and prove all of these fail with `IntegrityError`:

```python
def test_project_membership_cannot_cross_unit(postgres_session):
    seed_unit_member(postgres_session, user_id="u1", unit_id="unit-a")
    seed_project(postgres_session, project_id="p1", unit_id="unit-b")
    postgres_session.add(ProjectMembership(
        user_id="u1",
        unit_id="unit-a",
        project_id="p1",
        status="active",
    ))
    with pytest.raises(IntegrityError):
        postgres_session.flush()
```

Also prove a project role cannot bind to a unit membership, `custom_projects` cannot reference another unit, duplicate `(issuer, subject)` is rejected without case normalization, multiple active sessions may exist, and consumed/expired login transactions remain queryable for audit but cannot be reused. Update `test_postgres_migrations.py` in the same red step to expect revision `20260804_09`, exercise upgrade/downgrade/upgrade, and keep the pre-existing migration assertions.

Add `backend/tests/support/run_postgres_tests.ps1` as test infrastructure in the same step. It must contain this self-cleaning disposable database runner:

```powershell
param(
    [Parameter(Mandatory = $true)]
    [string[]]$PytestPath
)

$ErrorActionPreference = "Stop"
$iapContainerName = "iap-postgres-test-$([guid]::NewGuid().ToString('N'))"
$iapDatabase = "iap_auth_test"
$iapUser = "iap_auth_test"
$iapPassword = [guid]::NewGuid().ToString("N")
$iapContainerId = $null

try {
    docker container inspect $iapContainerName *> $null
    if ($LASTEXITCODE -eq 0) {
        throw "Disposable container name unexpectedly exists"
    }

    $iapContainerId = (docker run --detach --name $iapContainerName --env "POSTGRES_DB=$iapDatabase" --env "POSTGRES_USER=$iapUser" --env "POSTGRES_PASSWORD=$iapPassword" --publish "127.0.0.1::5432" postgres:16-alpine).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $iapContainerId) {
        throw "Disposable PostgreSQL startup failed"
    }

    $iapReady = $false
    for ($iapAttempt = 0; $iapAttempt -lt 60; $iapAttempt++) {
        docker exec $iapContainerId pg_isready -U $iapUser -d $iapDatabase *> $null
        if ($LASTEXITCODE -eq 0) {
            $iapReady = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $iapReady) { throw "Disposable PostgreSQL did not become ready" }

    $iapBinding = (docker port $iapContainerId 5432/tcp).Trim()
    if ($iapBinding -notmatch '^127\.0\.0\.1:(\d+)$') {
        throw "Unsafe PostgreSQL binding: $iapBinding"
    }

    $env:PYTHONPATH = "backend"
    $env:TEST_DATABASE_URL = (
        "postgresql+psycopg://{0}:{1}@127.0.0.1:{2}/{3}" -f
        $iapUser, $iapPassword, $Matches[1], $iapDatabase
    )
    & python -m pytest -q @PytestPath
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL test run failed" }
} finally {
    if ($iapContainerId) {
        $iapActualNameOutput = @(docker inspect --format '{{.Name}}' $iapContainerId)
        $iapInspectExit = $LASTEXITCODE
        if ($iapInspectExit -ne 0) { throw "Container identity inspection failed" }
        $iapActualName = (($iapActualNameOutput -join "").Trim().TrimStart("/"))
        if ($iapActualName -ne $iapContainerName) {
            throw "Container identity changed; refusing cleanup"
        }

        docker rm --force --volumes $iapContainerId
        $iapRemoveExit = $LASTEXITCODE
        $iapRemainingContainers = @(docker ps -aq --no-trunc --filter "id=$iapContainerId")
        $iapListExit = $LASTEXITCODE
        $iapRemainingContainers = @(
            $iapRemainingContainers | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
        )
        if ($iapRemoveExit -ne 0) { throw "Disposable PostgreSQL cleanup failed" }
        if ($iapListExit -ne 0) { throw "Failed to verify disposable PostgreSQL cleanup" }
        if ($iapRemainingContainers.Count -ne 0) {
            throw "Disposable PostgreSQL container still exists: $iapContainerId"
        }
    }
}
```

The runner creates `iap_auth_test` through the PostgreSQL entrypoint, publishes a random IPv4-loopback port, verifies the exact container ID/name before removal, checks the removal exit code, proves no matching container ID remains, and never attaches a persistent host or named volume.


- [ ] **Step 2: Run tests and verify failure**

Run unit tests:

```powershell
$env:PYTHONPATH = "backend"
python -m pytest -q backend/tests/identity/test_models.py
```

Run PostgreSQL constraints from the repository root with a disposable database:

```powershell
& ./backend/tests/support/run_postgres_tests.ps1 -PytestPath @('backend/tests/integration/test_identity_migrations.py', 'backend/tests/integration/test_postgres_migrations.py')
```

Expected: unit tests FAIL because the models are absent; PostgreSQL tests FAIL because revision `20260804_09` is absent.

- [ ] **Step 3: Implement exact tables and constraints**

Create these tables:

```text
users
external_identities
external_identity_history
units
projects
unit_memberships
project_memberships
roles
permissions
role_permissions
unit_membership_roles
project_membership_roles
role_permission_projects
menus
menu_permissions
oidc_login_transactions
auth_sessions
```

Key model fields:

```python
class Menu(Base):
    __tablename__ = "menus"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    node_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    route_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("menus.id"))
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    visibility_target: Mapped[str | None] = mapped_column(String(20))
    requires_current_project: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    unit_id: Mapped[str] = mapped_column(ForeignKey("units.id"), nullable=False)
    current_project_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    auth_method: Mapped[str] = mapped_column(String(20), nullable=False)
    csrf_secret_encrypted: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    provider_tokens_encrypted: Mapped[dict[str, str] | None] = mapped_column(JSON)
    provider_sid: Mapped[str | None] = mapped_column(String(255))
    authorization_version: Mapped[int] = mapped_column(Integer, nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(80))
```

`oidc_login_transactions` stores only hashes for state, nonce, and browser correlation, plus encrypted PKCE verifier, exact issuer/client/redirect values, safe relative `return_to`, expiry, and `consumed_at`.

Use composite unique constraints as referenced targets before adding composite foreign keys:

```text
projects(id, unit_id)
unit_memberships(user_id, unit_id)
roles(id, scope_type, unit_id)
role_permissions(id, unit_id)
```

Add CHECK constraints for role scope, membership status, session auth method, data scope, menu kind/route-key shape, menu visibility target, and project requirement. A group has `route_key IS NULL`, `visibility_target IS NULL`, and `requires_current_project=false`; a route has a non-null allowlisted `route_key` and `visibility_target IN ('unit', 'current_project')`. Every current-project route and the unit-target `chat` route require a project; other unit routes do not. Cross-table state checks remain in the Service layer; unit consistency remains in the database.

Add a nullable composite foreign key from `(auth_sessions.current_project_id, auth_sessions.unit_id)` to `(projects.id, projects.unit_id)`. Role binding tables carry a fixed `scope_type` column and use `(role_id, scope_type, unit_id)` composite foreign keys. Enforce the cross-table `custom_projects` rule with a PostgreSQL constraint trigger plus the same Service validation; a normal CHECK cannot inspect `role_permissions.data_scope`.

- [ ] **Step 4: Verify one Alembic head and downgrade safety**

Run:

```powershell
$env:PYTHONPATH = "backend"
python -m alembic -c backend/alembic.ini heads
python -m pytest -q backend/tests/identity/test_models.py
& ./backend/tests/support/run_postgres_tests.ps1 -PytestPath @('backend/tests/integration/test_identity_migrations.py', 'backend/tests/integration/test_postgres_migrations.py')
```

Expected: one head at `20260804_09`; model and both PostgreSQL migration test files PASS. The integration tests must upgrade from `20260804_08`, inspect constraints, downgrade to `20260804_08`, and upgrade again.

- [ ] **Step 5: Commit**

```powershell
git add -- backend/app/identity/__init__.py backend/app/identity/models.py backend/app/db/base.py backend/alembic/versions/20260804_09_identity_authorization_foundation.py backend/tests/identity/test_models.py backend/tests/integration/test_identity_migrations.py backend/tests/integration/test_postgres_migrations.py backend/tests/support/run_postgres_tests.ps1
git diff --cached --name-only
git commit -m "feat: add identity authorization schema"
```

Expected before commit: the staged-name output is exactly the eight Task 2 files above.

### Task 3: Migrate Audit Identity Snapshots And Auth Events

**Files:**

- Create: `backend/alembic/versions/20260804_10_audit_auth_contract.py`
- Modify: `backend/app/agents/service.py`
- Modify: `backend/app/audit/backfill.py`
- Modify: `backend/app/audit/management.py`
- Modify: `backend/app/audit/models.py`
- Modify: `backend/app/audit/recorder.py`
- Modify: `backend/app/audit/schemas.py`
- Modify: `backend/app/audit/policy.py`
- Modify: `backend/app/audit/repository.py`
- Modify: `backend/app/audit/service.py`
- Modify: `backend/app/conversations/models.py`
- Modify: `backend/app/conversations/repository.py`
- Modify: `backend/app/conversations/service.py`
- Modify: `backend/app/core/request_context.py`
- Modify: `backend/app/mcp/service.py`
- Modify: `backend/app/model_providers/service.py`
- Modify: `backend/app/runtime/harness.py`
- Modify: `backend/app/tools/gateway.py`
- Modify: `backend/app/tools/schemas.py`
- Modify: `backend/app/tools/service.py`
- Modify: `backend/README.md`
- Modify: `backend/tests/audit/test_api.py`
- Modify: `backend/tests/audit/test_backfill.py`
- Modify: `backend/tests/audit/test_models.py`
- Modify: `backend/tests/audit/test_recorder.py`
- Modify: `backend/tests/audit/test_repository.py`
- Modify: `backend/tests/audit/test_service.py`
- Modify: `backend/tests/conversations/test_models.py`
- Modify: `backend/tests/conversations/test_repository.py`
- Modify: `backend/tests/conversations/test_service.py`
- Modify: `backend/tests/core/test_request_context.py`
- Modify: `backend/tests/integration/test_audit_concurrency.py`
- Modify: `backend/tests/integration/test_postgres_migrations.py`
- Modify: `backend/tests/runtime/test_harness.py`
- Modify: `backend/tests/tools/test_gateway.py`
- Modify: `frontend/src/api/audit.ts`
- Modify: `frontend/src/api/audit.test.ts`
- Modify: `frontend/src/views/security/AuditLogView.vue`
- Modify: `frontend/src/views/security/AuditLogView.test.ts`

**Interfaces:**

- Replaces fixed `actor_role: str` snapshots with sorted `actor_roles_json: list[str]` on both `AuditEvent` and `AgentRun`; API schemas expose `actor_roles: list[str]`.
- Adds `authorization_scope: platform | unit | project | own | emergency | system`, `event_scope: platform | unit | project`, `auth_method`, `category=security`, and `source=auth`.
- Produces `RequestContext.role_codes: tuple[str, ...]` as the temporary sorted snapshot interface consumed by every existing producer; Task 5's `AuthorizationContext.role_codes` replaces the request source without changing the persisted runtime contract.
- Keeps `ToolExecutionContext` as a separate immutable runtime snapshot with non-null `project_id`, `run_id`, `conversation_id`, `unit_id`, `user_id`, `actor_roles`, and `timezone`; it never carries a browser session, CSRF secret, or live grant set.

- [ ] **Step 1: Write failing compatibility tests**

Assert migration maps `"project_admin,user"` to `["project_admin", "user"]`, maps `"unknown"` to an empty list, removes `ck_audit_actor_role`, and permits `unit_admin` without changing historical rows. Assert platform events require `unit_id IS NULL`, unit/project events require a unit, and project events require a project. Add compatibility tests for every current audit producer and reader: management services, Conversation/AgentRun creation, Repository execution-context loading, runtime Harness, Tool gateway, audit backfill, API serialization, and frontend audit display. PostgreSQL tests cover both the migration cycle and concurrent idempotent audit insertion.

Recorder test:

```python
request = AuditRecordRequest(
    unit_id="unit-1",
    project_id=None,
    user_id="user-1",
    actor_roles=("unit_admin",),
    authorization_scope="unit",
    event_scope="unit",
    auth_method="oidc",
    category="security",
    source="auth",
    action="auth.login.succeeded",
    status="succeeded",
    risk_level="medium",
    idempotency_key="auth:login:tx-1:succeeded",
    occurred_at=datetime.now(UTC),
)
event = AuditRecorder().record(session, request)
assert event.actor_roles_json == ["unit_admin"]
```

- [ ] **Step 2: Run and verify failure**

Run:

```powershell
$env:PYTHONPATH = "backend"
python -m pytest -q backend/tests/audit/test_models.py backend/tests/audit/test_recorder.py backend/tests/audit/test_repository.py backend/tests/conversations/test_models.py backend/tests/conversations/test_repository.py backend/tests/conversations/test_service.py backend/tests/runtime/test_harness.py backend/tests/tools/test_gateway.py
npm --prefix frontend test -- src/api/audit.test.ts src/views/security/AuditLogView.test.ts
```

Expected: FAIL because the current model has a fixed three-role CHECK and lacks authentication fields.

- [ ] **Step 3: Implement revision and recorder contract**

Revision `20260804_10` must:

1. Add JSON role snapshots and new scope/auth columns to `audit_events`.
2. Backfill deterministic role arrays and scopes.
3. Drop `ck_audit_actor_role` and the old `actor_role` column.
4. Add equivalent JSON role snapshots to `agent_runs`, backfill, and drop its old column.
5. Make `unit_id` nullable only for platform events using a CHECK constraint.
6. Add bounded CHECK constraints for `authorization_scope`, `event_scope`, `category`, and `source`.
7. Provide a downgrade that restores the old snapshot columns using only legacy-compatible values and documents that new role names collapse to `unknown`.

Update `AuditRecordRequest` to accept `actor_roles: tuple[str, ...]`, sort and deduplicate only after rejecting whitespace and unknown formatting, and never commit its Session. Give the legacy `RequestContext` only the explicit `role_codes` snapshot property needed by this migration; do not add any compatibility property for `project_id/role/roles` to the later `AuthorizationContext`.

Migrate every producer and reader in this task before the revision is committed. Conversation creation writes `AgentRun.actor_roles_json=list(context.role_codes)`; all management, Agent, MCP, model-provider, Tool, Harness, and backfill audit calls pass `actor_roles`. `ConversationRepository.get_run_execution_context()` reads the AgentRun JSON snapshot, and Harness constructs `ToolExecutionContext(actor_roles=...)` from the persisted Conversation/Run rather than from a live Web session. Historical backfill uses the stored run snapshot, with an empty list representing an unknown legacy role.

Update backend and frontend audit schemas from singular `actor_role` to `actor_roles`. The frontend renders the stable list and has no fallback that invents an administrator role. These are contract migrations in Task 3, not deferred cleanup.

- [ ] **Step 4: Run audit, migration, and runtime regression tests**

Run:

```powershell
$env:PYTHONPATH = "backend"
python -m pytest -q backend/tests/audit backend/tests/conversations backend/tests/runtime backend/tests/tools backend/tests/core/test_request_context.py backend/tests/test_agents.py backend/tests/test_mcp.py backend/tests/test_model_providers.py
```

With PostgreSQL:

```powershell
& ./backend/tests/support/run_postgres_tests.ps1 -PytestPath @('backend/tests/integration/test_postgres_migrations.py', 'backend/tests/integration/test_audit_concurrency.py')
```

Then verify the frontend contract:

```powershell
npm --prefix frontend test -- src/api/audit.test.ts src/views/security/AuditLogView.test.ts
npm --prefix frontend run build
```

Expected: all listed suites PASS with one Alembic head at `20260804_10`; neither backend nor frontend contains a singular runtime/API `actor_role` field.

- [ ] **Step 5: Commit**

```powershell
git add -- backend/alembic/versions/20260804_10_audit_auth_contract.py backend/app/agents/service.py backend/app/audit/backfill.py backend/app/audit/management.py backend/app/audit/models.py backend/app/audit/recorder.py backend/app/audit/schemas.py backend/app/audit/policy.py backend/app/audit/repository.py backend/app/audit/service.py backend/app/conversations/models.py backend/app/conversations/repository.py backend/app/conversations/service.py backend/app/core/request_context.py backend/app/mcp/service.py backend/app/model_providers/service.py backend/app/runtime/harness.py backend/app/tools/gateway.py backend/app/tools/schemas.py backend/app/tools/service.py backend/README.md backend/tests/audit/test_api.py backend/tests/audit/test_backfill.py backend/tests/audit/test_models.py backend/tests/audit/test_recorder.py backend/tests/audit/test_repository.py backend/tests/audit/test_service.py backend/tests/conversations/test_models.py backend/tests/conversations/test_repository.py backend/tests/conversations/test_service.py backend/tests/core/test_request_context.py backend/tests/integration/test_audit_concurrency.py backend/tests/integration/test_postgres_migrations.py backend/tests/runtime/test_harness.py backend/tests/tools/test_gateway.py frontend/src/api/audit.ts frontend/src/api/audit.test.ts frontend/src/views/security/AuditLogView.vue frontend/src/views/security/AuditLogView.test.ts
git diff --cached --name-only
git commit -m "feat: extend audit identity contract"
```

Expected before commit: the staged-name output is exactly the Task 3 Files list above.

### Task 4: Seed Stable Permissions, Menus, Roles, And The First Identity

**Files:**

- Create: `backend/app/identity/catalogue.py`
- Create: `backend/app/identity/bootstrap.py`
- Create: `backend/tests/identity/test_bootstrap.py`
- Modify: `backend/README.md`

**Interfaces:**

- Produces `seed_builtin_catalogue(session: Session, unit_id: str) -> None`.
- Produces `bootstrap_initial_unit_admin(session: Session, request: BootstrapRequest) -> str`.
- The command is invoked as `python -m app.identity.bootstrap` and reads exact issuer/subject and unit details from prompted input or a protected JSON file, never from a password argument.

- [ ] **Step 1: Write failing idempotency and binding tests**

Test exact cases:

- Running the seed twice creates no duplicate permission, role, menu, or grant.
- Built-in role codes and permission codes cannot be renamed or deleted.
- Bootstrap creates one active unit membership, one exact `(issuer, subject)` binding, and `unit_admin`.
- Email, display name, case folding, trimming, or issuer slash removal never merge identities.
- A second active unit membership for the same user is rejected in the first release.
- Bootstrap writes a redacted `auth.identity.bound` security event.
- Invalid or already-bound subjects roll back the whole transaction.
- Every seeded menu row has the closed-catalogue `requires_current_project` value; `chat` keeps unit visibility but requires a project, every current-project route requires one, and groups/other unit routes do not.

- [ ] **Step 2: Run and verify failure**

Run:

```powershell
$env:PYTHONPATH = "backend"
python -m pytest -q backend/tests/identity/test_bootstrap.py
```

Expected: FAIL because the catalogue and bootstrap command do not exist.

- [ ] **Step 3: Implement the initial stable catalogue**

Use dot-form permission codes:

```python
PERMISSION_CODES = (
    "platform.read",
    "dashboard.read",
    "project.read",
    "project.manage",
    "project.member.manage",
    "identity.read",
    "identity.manage",
    "agent.read",
    "agent.manage",
    "agent.run",
    "conversation.read",
    "conversation.manage",
    "workflow.read",
    "workflow.manage",
    "workflow.run",
    "knowledge.read",
    "knowledge.manage",
    "knowledge.retrieve",
    "model.read",
    "model.manage",
    "model.run",
    "tool.read",
    "tool.manage",
    "tool.invoke",
    "mcp.read",
    "mcp.manage",
    "mcp.sync",
    "skill.read",
    "skill.manage",
    "skill.invoke",
    "collaboration.read",
    "collaboration.manage",
    "collaboration.run",
    "prompt.read",
    "prompt.manage",
    "resource.read",
    "resource.manage",
    "resource.publish",
    "resource.review",
    "artifact.read",
    "artifact.manage",
    "approval.read",
    "approval.manage",
    "policy.read",
    "policy.manage",
    "credential.read",
    "credential.manage",
    "settings.read",
    "settings.manage",
    "audit.read",
    "sandbox.read",
    "integration.read",
    "integration.manage",
)
```

Seed built-ins `unit_admin`, `project_admin`, `business_operator`, `model_expert`, `unit_auditor`, and `viewer`. `unit_admin` receives every code with `unit` scope. Use these explicit non-admin code sets:

```python
ROLE_PERMISSION_CODES = {
    "project_admin": (
        "dashboard.read", "project.read", "project.manage",
        "project.member.manage", "agent.read", "agent.manage", "agent.run",
        "conversation.read", "conversation.manage", "workflow.read",
        "workflow.manage", "workflow.run", "knowledge.read",
        "knowledge.manage", "knowledge.retrieve", "model.read", "model.manage",
        "model.run", "tool.read", "tool.invoke", "mcp.read", "skill.read",
        "skill.invoke", "collaboration.read", "collaboration.manage",
        "collaboration.run", "prompt.read", "prompt.manage", "resource.read",
        "resource.manage", "resource.publish", "artifact.read",
        "artifact.manage", "approval.read", "integration.read",
    ),
    "business_operator": (
        "dashboard.read", "project.read", "agent.read", "agent.run",
        "conversation.read", "workflow.read", "workflow.run",
        "knowledge.read", "knowledge.retrieve", "model.read", "model.run",
        "tool.read", "tool.invoke", "skill.read", "skill.invoke",
        "collaboration.read", "collaboration.run", "resource.read",
        "artifact.read", "approval.read",
    ),
    "model_expert": (
        "dashboard.read", "project.read", "agent.read", "agent.run",
        "conversation.read", "workflow.read", "workflow.run",
        "knowledge.read", "knowledge.manage", "knowledge.retrieve",
        "model.read", "model.run", "tool.read", "tool.invoke", "skill.read",
        "skill.invoke", "prompt.read", "prompt.manage", "resource.read",
        "resource.manage", "artifact.read", "artifact.manage",
    ),
    "unit_auditor": (
        "platform.read", "dashboard.read", "project.read", "identity.read",
        "agent.read", "conversation.read", "workflow.read", "knowledge.read",
        "model.read", "tool.read", "mcp.read", "skill.read",
        "collaboration.read", "prompt.read", "resource.read", "artifact.read",
        "approval.read", "policy.read", "audit.read", "sandbox.read",
        "integration.read", "settings.read",
    ),
    "viewer": (
        "dashboard.read", "project.read", "agent.read", "conversation.read",
        "workflow.read", "knowledge.read", "model.read", "tool.read",
        "skill.read", "collaboration.read", "resource.read", "artifact.read",
    ),
}
```

Use `unit` scope for `unit_auditor`; use `project` scope for the four project roles. Add a separate `conversation.manage + own` tuple to `business_operator`. No built-in role receives an implicit wildcard.

Seed all six groups and every leaf in the complete Route And Menu Contract near the top of this plan. `menu_permissions` maps navigation to permission codes, while each leaf's `visibility_target` keeps project grants from exposing unit-global pages. Seed `requires_current_project` from the same closed catalogue, including the special unit-target `chat` leaf; do not infer it from a server label and store no component path.

- [ ] **Step 4: Implement atomic offline bootstrap**

`BootstrapRequest` contains:

```python
@dataclass(frozen=True)
class BootstrapRequest:
    unit_code: str
    unit_name: str
    user_display_name: str
    issuer: str
    subject: str
    initial_project_code: str
    initial_project_name: str
```

Generate local UUIDs, preserve issuer and subject exactly, create the unit/project/memberships and external identity, call `seed_builtin_catalogue`, bind `unit_admin`, increment `authorization_version`, record audit, and commit once. Never accept or create a normal-user password.

- [ ] **Step 5: Run tests and commit**

Run:

```powershell
$env:PYTHONPATH = "backend"
python -m pytest -q backend/tests/identity/test_bootstrap.py backend/tests/audit/test_recorder.py
```

Expected: PASS.

```powershell
git add -- backend/app/identity/catalogue.py backend/app/identity/bootstrap.py backend/tests/identity/test_bootstrap.py backend/README.md
git diff --cached --name-only
git commit -m "feat: bootstrap identity authorization catalogue"
```

Expected before commit: the staged-name output is exactly the four Task 4 files above.

### Task 5: Implement Permission-And-Scope Authorization

**Files:**

- Create: `backend/app/identity/authorization.py`
- Create: `backend/app/identity/repository.py`
- Create: `backend/app/identity/dependencies.py`
- Create: `backend/app/identity/schemas.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/identity/test_authorization.py`
- Create: `backend/tests/identity/test_project_context.py`
- Modify: `backend/app/core/request_context.py`
- Modify: `backend/app/audit/policy.py`
- Modify: `backend/tests/core/test_request_context.py`

**Interfaces:**

- Produces `AuthorizationContext`, `PermissionGrant`, `PermissionCapability`, `ResourceScope`, `AuthorizationService.load_context()`, `AuthorizationService.allows()`, `AuthorizationService.allows_entry()`, `AuthorizationService.entry_capabilities()`, `require_authorization_context`, `require_project_context`, `require_scoped_permission(code)`, and `require_permission(code, project_required=False)`.
- All HTTP request authorization consumers migrate from `RequestContext` to `AuthorizationContext`. The development/test adapter in `backend/app/core/request_context.py` returns the same type; do not retain `.project_id`, `.role`, or `.roles` compatibility properties. Project consumers use `require_project_context` and then read non-null `current_project_id`; unit consumers accept it as nullable. Persisted asynchronous runs continue using the separate `ToolExecutionContext` snapshot defined in Task 3.

- [ ] **Step 1: Write failing tuple-union and project-context tests**

The central regression must prove no privilege multiplication:

```python
context = context_with_grants(
    PermissionGrant("agent.run", "own", frozenset({"project-1"}), "user-1"),
    PermissionGrant("agent.read", "unit", frozenset(), None),
)
assert service.allows(
    context,
    "agent.run",
    ResourceScope("unit-1", "project-1", "user-1"),
)
assert not service.allows(
    context,
    "agent.run",
    ResourceScope("unit-1", "project-1", "user-2"),
)
assert service.allows(
    context,
    "agent.read",
    ResourceScope("unit-1", "project-2", "user-2"),
)
```

Also test inactive users/members/projects/roles/grants never contribute, a multi-project user begins with `current_project_id=None`, a one-project user may auto-select, `own` intersects its role boundary, `custom_projects` cannot cross units, and authorization-version changes reload context. A stale non-null session project whose Project or ProjectMembership is inactive must never enter `AuthorizationContext`; project admission returns 409 while unit admission remains valid after reconciliation. Prove a project-only grant cannot enter a unit target, a unit grant can enter either target, an `own` grant may enter only a covered current project, and that route entry still cannot authorize another owner's object.

Assert `entry_capabilities()` returns unique, sorted `PermissionCapability(code, target)` pairs, returns no `current_project` target when no project is selected, and emits both `unit` and `current_project` for a unit grant when a valid project is selected. Assert `require_scoped_permission("audit.read")` admits unit, project, and own grants without claiming that any audit object is authorized. Assert the new context exposes `current_project_id` but no `project_id/role/roles` attributes.

- [ ] **Step 2: Run and verify failure**

Run:

```powershell
$env:PYTHONPATH = "backend"
python -m pytest -q backend/tests/identity/test_authorization.py backend/tests/identity/test_project_context.py backend/tests/core/test_request_context.py
```

Expected: FAIL because authorization still derives from development role strings.

- [ ] **Step 3: Implement immutable grant predicates**

Use these exact domain types:

```python
DataScope = Literal["unit", "assigned_projects", "project", "own", "custom_projects"]

@dataclass(frozen=True)
class PermissionGrant:
    permission_code: str
    data_scope: DataScope
    project_ids: frozenset[str]
    owner_user_id: str | None

@dataclass(frozen=True)
class ResourceScope:
    unit_id: str
    project_id: str | None
    owner_user_id: str | None

PermissionTarget = Literal["unit", "current_project"]

@dataclass(frozen=True, order=True)
class PermissionCapability:
    code: str
    target: PermissionTarget

class AuthorizationContext(BaseModel):
    session_id: str
    user_id: str
    unit_id: str
    current_project_id: str | None
    auth_method: Literal["oidc", "dev_test"]
    authorization_version: int
    role_codes: tuple[str, ...]
    grants: tuple[PermissionGrant, ...]
```

`allows()` iterates only grants for the requested permission and evaluates that grant's own predicate against an actual `ResourceScope`. Unit matching is a mandatory outer condition. `AuthorizationService.load_context()` obtains a selected project only through an active same-user/same-unit ProjectMembership joined to an active Project; it never copies an unchecked `AuthSession.current_project_id`. `require_project_context` returns `409 AUTH_CONTEXT_CHANGED` when no valid project is selected; it never chooses the first project.

`require_permission(code, project_required=False)` is a route-admission dependency, not an object authorization shortcut. With `project_required=False`, it evaluates the trusted unit target `ResourceScope(context.unit_id, None, None)`; project, custom-project, assigned-project, and own-only grants do not qualify. With `project_required=True`, it first requires `current_project_id`, then `allows_entry()` accepts only a same-unit grant whose own project boundary covers that current project. An `own` grant is sufficient to enter that route but never proves ownership of an arbitrary object.


`require_scoped_permission(code)` is reserved for collection APIs, such as audit, whose SQL layer compiles the caller's matching grant predicates. It verifies only that at least one active same-unit tuple exists and returns `AuthorizationContext`; it must never be used as proof for a loaded object. `entry_capabilities()` evaluates exactly the two trusted entry targets and returns the sorted pairs used by `/api/auth/me`.
The dependency never reads unit, project, owner, role, or scope from Headers, query parameters, path parameters, or request bodies. A detail endpoint loads the object through a scoped Repository and calls `allows()` with its actual `(unit_id, project_id, owner_user_id)`; a collection applies the equivalent SQL predicate. Missing or unauthorized objects remain safe 404. Tests assert the response order is unauthenticated 401, authenticated with no required project 409, authenticated with an insufficient target grant 403, and scoped object denial 404.

Replace audit role-name policy with `audit.read` plus each matching grant's own predicate. `audit_scope_predicate(context)` always includes the current unit boundary, ORs only `audit.read` tuples, maps project/custom/assigned scopes to their exact project sets, and maps `own` to both its project boundary and `AuditEvent.user_id == context.user_id`. An empty set compiles to SQL false.

- [ ] **Step 4: Add dependency override fixtures**

`backend/tests/conftest.py` must provide factories for an authenticated unit context, project context, unit administrator, project administrator, auditor, and viewer. Existing API tests consume dependency overrides rather than sending identity headers. Keep a small isolated test suite for the development adapter itself.

- [ ] **Step 5: Run focused and audit tests, then commit**

Run:

```powershell
$env:PYTHONPATH = "backend"
python -m pytest -q backend/tests/identity/test_authorization.py backend/tests/identity/test_project_context.py backend/tests/core/test_request_context.py backend/tests/audit
```

Expected: PASS.

```powershell
git add -- backend/app/identity/authorization.py backend/app/identity/repository.py backend/app/identity/dependencies.py backend/app/identity/schemas.py backend/tests/conftest.py backend/tests/identity/test_authorization.py backend/tests/identity/test_project_context.py backend/app/core/request_context.py backend/app/audit/policy.py backend/tests/core/test_request_context.py
git diff --cached --name-only
git commit -m "feat: compute scoped authorization grants"
```

Expected before commit: the staged-name output is exactly the ten Task 5 files above.

### Task 6: Implement Opaque Sessions And Stable CSRF Tokens

**Files:**

- Create: `backend/app/identity/sessions.py`
- Create: `backend/app/identity/csrf.py`
- Create: `backend/tests/identity/test_sessions.py`
- Create: `backend/tests/identity/test_csrf.py`

**Interfaces:**

- Produces `IssuedSession`, `AuthenticatedSession`, `ProjectReconciliationResult`, `SessionService.issue_oidc_session()`, `SessionService.authenticate(db: Session, raw_cookie: str) -> AuthenticatedSession | None`, `SessionService.reconcile_current_project(db: Session, auth_session: AuthSession) -> ProjectReconciliationResult`, `SessionService.revoke()`, `SessionService.touch_if_due()`, `derive_csrf_token()`, `verify_csrf_token()`, and `verify_browser_request_provenance(request: Request, expected_origin: str) -> bool`.
- Task 8 sets and clears Cookies and owns the transaction commit; the session service never writes a browser response or commits.

- [ ] **Step 1: Write failing session lifecycle tests**

Test exact behavior:

- The raw 256-bit Cookie value never appears in the database.
- Tampered and unknown Cookies return no session.
- Idle expiry is 30 minutes; absolute expiry is 8 hours.
- `last_seen_at` updates at most once per five minutes.
- User disable, membership disable, explicit revocation, and absolute expiry reject the session.
- A temporary Provider network failure does not revoke an otherwise valid local session.
- Authorization-version mismatch reloads authorization; inactive identity revokes.
- Session rotation invalidates the prior Cookie.
- Disabling the selected Project or its same-user/same-unit ProjectMembership clears `current_project_id` under a row lock without revoking the unit session or selecting another still-valid project; disabling the unit membership still revokes the session. The result is respectively `(changed=true, old_project_id='p1', current_project_id=None, reason='project_inactive')` or the same tuple with `membership_inactive`.
- A valid unchanged project returns `(changed=false, old_project_id='p1', current_project_id='p1', reason=None)`. `authenticate()` invokes reconciliation exactly once for every valid Cookie even when `authorization_version` is unchanged, and returns that same structured result to its caller.

CSRF tests must prove the same session returns the same HMAC Token across calls/tabs, another session returns a different Token, constant-time validation rejects a modified Token, and revocation invalidates it.

Browser-provenance tests accept only one Origin exactly equal to normalized `Settings.public_base_url` parsed from `IAP_PUBLIC_BASE_URL`, plus exactly one `Sec-Fetch-Site: same-origin`, one `Sec-Fetch-Mode: cors|same-origin`, and one `Sec-Fetch-Dest: empty`. Reject missing/duplicate/malformed/null/cross-origin Origin, missing or duplicate Fetch Metadata, `cross-site/same-site/none`, other modes or destinations, and any attempt to derive the origin from Host or forwarding Headers.

- [ ] **Step 2: Run and verify failure**

Run:

```powershell
$env:PYTHONPATH = "backend"
python -m pytest -q backend/tests/identity/test_sessions.py backend/tests/identity/test_csrf.py
```

Expected: FAIL because session services do not exist.

- [ ] **Step 3: Implement issuance, authentication, and CSRF**

Core issuance contract:

```python
@dataclass(frozen=True)
class IssuedSession:
    raw_cookie: str
    record: AuthSession

ProjectReconciliationReason = Literal["project_inactive", "membership_inactive"]

@dataclass(frozen=True)
class ProjectReconciliationResult:
    changed: bool
    old_project_id: str | None
    current_project_id: str | None
    reason: ProjectReconciliationReason | None

@dataclass(frozen=True)
class AuthenticatedSession:
    record: AuthSession
    project_reconciliation: ProjectReconciliationResult


def derive_csrf_token(secret: bytes, session_id: str) -> str:
    digest = hmac.new(secret, f"{session_id}csrf-v1".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")
```

Generate the raw session with `secrets.token_urlsafe(32)`, store only its HMAC hash, encrypt the CSRF secret and minimal Provider Token set with `EnvelopeCipher`, and use timezone-aware UTC values. Authentication queries by hash, checks revocation/expiry/status, and updates `last_seen_at` only when due.

On every successful Cookie authentication, after validating the user and unit membership, `authenticate()` calls `reconcile_current_project()` exactly once even when `authorization_version` is unchanged. Reconciliation locks the AuthSession row and retains a project only when an active Project and active same-user/same-unit ProjectMembership both exist. Otherwise it stores NULL and returns the old project ID plus the deterministic reason: missing/inactive Project takes `project_inactive`; only an active Project with missing/inactive membership takes `membership_inactive`. It never commits or auto-selects a replacement. Task 8 consumes the returned result in the same transaction and never calls reconciliation again.

`verify_browser_request_provenance(request, expected_origin)` reads raw ASGI headers so duplicates cannot collapse. It compares the one Origin with startup-normalized `Settings.public_base_url` and enforces the exact Fetch Metadata values tested above; it never derives origin from Host, `Forwarded`, `X-Forwarded-*`, or `X-Real-IP`. Task 8 passes that setting and invokes the helper together with `verify_csrf_token()` only for Cookie-authenticated unsafe methods.

- [ ] **Step 4: Run tests and commit**

Run:

```powershell
$env:PYTHONPATH = "backend"
python -m pytest -q backend/tests/identity/test_sessions.py backend/tests/identity/test_csrf.py
```

Expected: PASS.

```powershell
git add -- backend/app/identity/sessions.py backend/app/identity/csrf.py backend/tests/identity/test_sessions.py backend/tests/identity/test_csrf.py
git diff --cached --name-only
git commit -m "feat: add opaque auth sessions"
```

Expected before commit: the staged-name output is exactly the four Task 6 files above.

### Task 7: Implement OIDC Transactions And Protocol Validation

**Files:**

- Create: `backend/app/identity/oidc.py`
- Create: `backend/tests/support/mock_oidc_provider.py`
- Create: `backend/tests/identity/test_oidc_client.py`
- Create: `backend/tests/integration/test_oidc_mock_flow.py`

**Interfaces:**

- Produces `OidcLoginRequest`, `VerifiedOidcIdentity`, `OidcTransactionService.start()`, `OidcTransactionService.consume()`, `OidcClient.authorization_url()`, and `OidcClient.exchange_and_validate()`.
- `OidcClient` accepts an injected synchronous `httpx.Client`; production uses a bounded client and tests use MockTransport or the protocol-level test server.

- [ ] **Step 1: Build the failing protocol test matrix**

The Mock Provider generates a test-only RSA key at process startup and exposes Discovery, JWKS, authorize, token, and UserInfo endpoints. Authorization Codes are one-time, five-minute records bound to redirect URI, client ID, and PKCE S256.

Write named cases for:

```text
valid_code_flow
state_reuse
missing_browser_correlation
wrong_browser_correlation
nonce_mismatch
issuer_mismatch
audience_mismatch
multi_audience_without_azp
wrong_azp
alg_none
expired_id_token
future_iat
userinfo_subject_mismatch
unknown_kid_single_refresh
unknown_kid_after_refresh
invalid_grant
token_timeout
unsafe_return_to
```

- [ ] **Step 2: Run and verify failure**

Run:

```powershell
$env:PYTHONPATH = "backend"
python -m pytest -q backend/tests/identity/test_oidc_client.py backend/tests/integration/test_oidc_mock_flow.py
```

Expected: FAIL because OIDC transaction and client modules are absent.

- [ ] **Step 3: Implement one-time login transactions**

`start()` validates a site-relative `return_to`, generates independent state, nonce, verifier, and browser correlation values, stores only hashes except the encrypted verifier, and expires the row after five minutes.

`consume()` uses a transaction and row lock, verifies state and browser-correlation hashes with constant-time comparison, checks exact issuer/client/redirect values and expiry, sets `consumed_at`, and cannot be called twice.

- [ ] **Step 4: Implement strict OIDC validation**

Use Authlib JOSE primitives for signature and registered Claim validation. Discovery issuer must equal configured issuer exactly. Accept only the configured asymmetric algorithm allowlist; reject `none`. Validate `iss`, `aud`, `azp`, `exp`, `iat`, optional `nbf`, and nonce. Refresh JWKS at most once for an unknown `kid`.

After validation, query UserInfo only when configured and require its `sub` to match. Return:

```python
@dataclass(frozen=True)
class VerifiedOidcIdentity:
    issuer: str
    subject: str
    sid: str | None
    display_name: str | None
    email: str | None
    provider_tokens: Mapping[str, str] = field(repr=False)
```

The caller persists only the Token fields required for refresh/UserInfo/logout; no Token enters logs, errors, redirects, or audit metadata.

- [ ] **Step 5: Run tests and commit**

Run:

```powershell
$env:PYTHONPATH = "backend"
python -m pytest -q backend/tests/identity/test_oidc_client.py backend/tests/integration/test_oidc_mock_flow.py
```

Expected: PASS.

```powershell
git add -- backend/app/identity/oidc.py backend/tests/support/mock_oidc_provider.py backend/tests/identity/test_oidc_client.py backend/tests/integration/test_oidc_mock_flow.py
git diff --cached --name-only
git commit -m "feat: validate oidc login transactions"
```

Expected before commit: the staged-name output is exactly the four Task 7 files above.

### Task 8: Expose Auth APIs, Middleware, Project Switching, And Audit

**Files:**

- Create: `backend/app/identity/router.py`
- Create: `backend/app/identity/middleware.py`
- Modify: `backend/app/identity/dependencies.py`
- Modify: `backend/app/identity/schemas.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/core/request_context.py`
- Create: `backend/tests/identity/test_auth_api.py`
- Modify: `backend/tests/identity/test_project_context.py`
- Modify: `backend/tests/test_main.py`

**Interfaces:**

- Produces `/api/auth/login`, `/api/auth/callback`, `/api/auth/me`, `/api/auth/logout`, and `/api/auth/context/project`.
- Produces centralized JSON errors `{code, message, request_id}` and a request-state authenticated session consumed by authorization dependencies.

- [ ] **Step 1: Write failing endpoint and middleware tests**

Cover:

- Login returns a Provider redirect and the path-limited transaction Cookie.
- Callback clears the transaction Cookie, sets a rotated session Cookie, and returns HTTP 303 to the stored relative path.
- Unbound identity, disabled user, missing unit membership, multiple active units, and invalid callback return stable errors without Provider details.
- `/auth/me` returns no Token and has `Cache-Control: no-store`.
- Multi-project users receive `current_project: null`; one-project users may receive that project.
- `/auth/me.permissions` returns sorted scoped capabilities, excludes `current_project` targets when the project is null, and never exposes a bare permission string.
- `/auth/me.menus` omits every `requires_current_project` leaf, including unit-target `chat`, when the current project is null; it includes Chat only for a selected valid project plus unit-target `agent.run`.
- If the selected Project or ProjectMembership becomes inactive, the next request calls `reconcile_current_project()` exactly once through `authenticate()`, atomically clears it, writes one `auth.project.cleared` event with the returned old ID and exact reason, and returns `current_project:null`; no project capability/Chat menu survives and the unit session remains active. Audit failure rolls back both changes.
- A valid unchanged-project request with `last_seen_at` older than five minutes commits the touched timestamp so a fresh Session can read it; a not-yet-due request leaves it unchanged. A request rejected by CSRF/Origin/Fetch Metadata rolls back any tentative touch and does not extend idle expiry.
- Project switch rejects foreign/inactive projects and returns a refreshed `AuthMe`.
- Logout revokes locally and clears Cookie even when the Provider is unavailable.
- Unsafe Cookie requests fail with 403 before the route handler when the CSRF Header is missing/invalid, Origin is missing/duplicate/null/malformed/not the configured origin, or any required Fetch Metadata Header is missing/duplicate/invalid.
- The valid CSRF + Origin + Fetch Metadata tuple succeeds; safe methods and requests without the browser session Cookie are not subjected to this middleware branch. Only the exact future back-channel and pre-session emergency login paths are exempt.
- Back-channel paths without Cookie are not accepted by this phase.
- Auth events contain no Cookie, Token, code, PKCE verifier, or full Claims.

- [ ] **Step 2: Run and verify failure**

Run:

```powershell
$env:PYTHONPATH = "backend"
python -m pytest -q backend/tests/identity/test_auth_api.py backend/tests/identity/test_project_context.py backend/tests/test_main.py
```

Expected: FAIL because the auth router and middleware are absent.

- [ ] **Step 3: Implement the response contract and Cookies**

`GET /api/auth/me` returns:

```python
MenuGroupKey = Literal["agents", "capabilities", "resources", "operations", "security", "system"]

class MenuRouteNode(BaseModel):
    kind: Literal["route"] = "route"
    key: str
    route_key: str
    title: str

class MenuGroupNode(BaseModel):
    kind: Literal["group"] = "group"
    key: MenuGroupKey
    title: str
    children: list[MenuRouteNode]

MenuNode = Annotated[MenuRouteNode | MenuGroupNode, Field(discriminator="kind")]

class AuthMe(BaseModel):
    user: UserSummary
    unit: UnitSummary
    current_project: ProjectSummary | None
    projects: list[ProjectSummary]
    roles: list[str]
    permissions: list[PermissionCapability]
    menus: list[MenuNode]
    authorization_version: int
    csrf_token: str
    session: SessionSummary
```

Production Cookie helpers use the exact names and attributes in Global Constraints. Explicit development/test loopback mode uses `iap_session` and `iap_oidc_tx` without `Secure`; production cannot select these names.

Menu serialization filters by the indivisible capability pair and `Menu.requires_current_project`. The frontend receives only the strict group/route union; it does not receive or choose the project-required flag.

- [ ] **Step 4: Implement authentication and CSRF middleware**

Middleware responsibilities, in order:

1. Attach a request ID.
2. Resolve the opaque session Cookie by calling `SessionService.authenticate()` exactly once without trusting user/project/role Headers; never call `reconcile_current_project()` separately.
3. Reject an absent, expired, or revoked `AuthenticatedSession`.
4. Consume its `ProjectReconciliationResult`. When `changed=true`, stage the required redacted `auth.project.cleared` event using its old project ID and reason in the same Session; do not commit yet.
5. For Cookie-authenticated unsafe methods, require all three checks before the route handler: `verify_csrf_token()`, exact configured Origin, and fail-closed Fetch Metadata through `verify_browser_request_provenance()`. Exclude only the exact future back-channel and pre-session emergency login paths.
6. After successful authentication and request-security checks, commit the authentication Session on every valid request before constructing `AuthorizationContext`. This persists a due `last_seen_at` even when reconciliation is unchanged; when changed, the project NULL and audit event commit atomically. Authentication, provenance, or required-audit failure rolls back all tentative session changes.
7. Attach only the committed `AuthenticatedSession.record` to `request.state`.
8. Add `Cache-Control: no-store` to auth responses.
9. Return stable redacted errors.

The development identity adapter remains a dependency fallback only when `IAP_ENVIRONMENT` is `development` or `test`, `IAP_ALLOW_DEV_IDENTITY=true`, and the direct peer is loopback. Production startup rejects the configuration.

- [ ] **Step 5: Record authentication events atomically**

Record `auth.login.started/succeeded/failed`, `auth.logout.succeeded`, `auth.project.switched/denied`, `auth.project.cleared`, and session revocation/expiry with exact scopes, idempotency keys, safe error codes, and hashed Provider `sid` only. `auth.project.cleared` consumes the single authentication result and records only its old project ID and `project_inactive` or `membership_inactive`; it never re-runs reconciliation or infers a reason after mutation. The middleware's unconditional successful-authentication commit also persists a due session touch. Authentication succeeds or fails independently of optional audit-read UI availability, but required security audit insert failure rolls back identity binding, session creation, role change, project reconciliation, and project switch transactions.

- [ ] **Step 6: Run tests and commit**

Run:

```powershell
$env:PYTHONPATH = "backend"
python -m pytest -q backend/tests/identity backend/tests/audit backend/tests/test_main.py
```

Expected: PASS.

```powershell
git add -- backend/app/identity/router.py backend/app/identity/middleware.py backend/app/identity/dependencies.py backend/app/identity/schemas.py backend/app/main.py backend/app/core/request_context.py backend/tests/identity/test_auth_api.py backend/tests/identity/test_project_context.py backend/tests/test_main.py
git diff --cached --name-only
git commit -m "feat: expose oidc bff auth api"
```

Expected before commit: the staged-name output is exactly the nine Task 8 files above.

### Task 9: Gate Every Existing Backend Router During Migration

**Files:**

- Modify: `backend/app/conversations/router.py`
- Modify: `backend/app/conversations/service.py`
- Modify: `backend/app/agents/router.py`
- Modify: `backend/app/agents/service.py`
- Modify: `backend/app/mcp/router.py`
- Modify: `backend/app/mcp/service.py`
- Modify: `backend/app/tools/router.py`
- Modify: `backend/app/tools/service.py`
- Modify: `backend/app/model_providers/router.py`
- Modify: `backend/app/model_providers/service.py`
- Modify: `backend/app/skills/router.py`
- Modify: `backend/app/platform/router.py`
- Modify: `backend/app/audit/router.py`
- Modify: `backend/app/audit/service.py`
- Modify: `backend/app/audit/policy.py`
- Modify: `backend/app/audit/repository.py`
- Modify: `backend/app/audit/management.py`
- Modify: `backend/app/core/request_context.py`
- Modify: `backend/tests/test_agents.py`
- Modify: `backend/tests/test_mcp.py`
- Modify: `backend/tests/tools/test_api.py`
- Modify: `backend/tests/test_model_providers.py`
- Modify: `backend/tests/test_skills.py`
- Modify: `backend/tests/test_platform.py`
- Modify: `backend/tests/conversations/test_api.py`
- Modify: `backend/tests/conversations/test_service.py`
- Modify: `backend/tests/audit/test_api.py`
- Modify: `backend/tests/audit/test_repository.py`
- Modify: `backend/tests/audit/test_service.py`
- Modify: `backend/tests/core/test_request_context.py`

**Interfaces:**

- Consumes `AuthorizationContext`, `require_permission()`, `require_scoped_permission()`, and `require_project_context()`.
- Produces a deny-by-default authenticated API surface, grant-derived audit SQL predicates, and a unit-gated global-Agent execution boundary while the resource-scope plan remains a production release prerequisite.
- Only HTTP request authorization consumers accept `AuthorizationContext`: mounted routers, request-path services, management-audit helpers, and authorization-aware repositories. `ToolGateway`, built-in executors, `PlatformAgentHarness`, and `ConversationRepository.get_run_execution_context()` continue using the persisted `ToolExecutionContext`/run snapshot from Task 3; they never receive a Cookie session or live grants.

- [ ] **Step 1: Write failing route matrix tests**

Build one parameterized expectation for every method/path in the matrix below and fail if any mounted non-auth route is missing or an unexpected route is added. Prove anonymous requests return 401, insufficient target grants return 403, project-required routes without a current project return 409, and `/api/health` alone remains public. Prove Skills and `/api/platform/overview` are no longer anonymous. Replace Header-based test setup with dependency overrides from `backend/tests/conftest.py`.

Add a regression in `backend/tests/conversations/test_api.py` that gives a user a selected project and only project-target `agent.run`, submits both a default-Agent message and a guessed global `actor_id`, and receives 403 before `AgentService.get_default/get`, model routing, tool resolution, or `RunDispatcher.dispatch` is called. A unit-target `agent.run` with no selected project returns `409 AUTH_CONTEXT_CHANGED`; the same unit grant with a selected project reaches the service.

Add a real-session parameterized `U+PC` regression that does not override `require_authorization_context`: seed a Cookie-backed `AuthSession` with selected `p1` and unit-target `agent.run`, then independently make the Project or the same-user/same-unit ProjectMembership inactive. For both `POST /api/conversations` and `POST /api/conversations/{id}/messages`, the next request must persist `auth_sessions.current_project_id=NULL` and return `409 AUTH_CONTEXT_CHANGED` before Conversation mutation or lookup, Agent resolution, model/tool resolution, or dispatch. Assert that no Conversation, Message, AgentRun, or RunEvent is added. Use the real `SessionService`/middleware/dependency chain and override only the database Session factory and external Agent dependencies.

Use the following complete transitional route matrix. `U` means `ResourceScope(context.unit_id, None, None)`. `P` means a selected current project and a matching project-entry grant. `U+PC` means a unit-target permission plus the independent presence of a selected current project. `P->O` additionally loads the real object through its unit/project/owner predicate and returns safe 404 on denial. `G/O` means scoped-grant admission, grant-to-SQL filtering, and then an actual-object check. A `+` means every listed permission is required.

| Conversation/Run operation | Permission | Target |
| --- | --- | --- |
| `POST /api/conversations` | `agent.run` | `U+PC`; new owner is the current user |
| `GET /api/conversations` | `conversation.read` | `P`; Repository applies owner predicate |
| `GET /api/conversations/{conversation_id}` | `conversation.read` | `P->O` |
| `GET /api/conversations/{conversation_id}/messages` | `conversation.read` | `P->O` |
| `POST /api/conversations/{conversation_id}/messages` | `agent.run` | `U+PC->O`; authorize the owned Conversation before resolving any global Agent; run actor is the current user |
| `GET /api/agent-runs` | `conversation.read` | `P`; Repository applies owner predicate |
| `GET /api/agent-runs/{run_id}` | `conversation.read` | `P->O` through its Conversation |
| `GET /api/agent-runs/{run_id}/tool-invocations` | `conversation.read` | `P->O` through its Conversation |
| `GET /api/agent-runs/{run_id}/events` | `conversation.read` | `P->O` through its Conversation |

`conversation.manage` is reserved for future rename/archive/delete operations. Creating a conversation and submitting a message remain part of `agent.run`, but this foundation intentionally accepts only a unit-target tuple because the selected Agent and its model/tool/knowledge configuration are global.

All current Agent records are global configuration in this phase, so each Agent operation requires `agent.manage` against `U`:

| Agent operation | Permission/target |
| --- | --- |
| `GET /api/agents` | `agent.manage`, `U` |
| `POST /api/agents` | `agent.manage`, `U` |
| `GET /api/agents/default` | `agent.manage`, `U` |
| `PUT /api/agents/default` | `agent.manage`, `U` |
| `GET /api/agents/{agent_id}` | `agent.manage`, `U` |
| `PUT /api/agents/{agent_id}` | `agent.manage`, `U` |
| `PATCH /api/agents/{agent_id}/toggle` | `agent.manage`, `U` |
| `PATCH /api/agents/{agent_id}/pin` | `agent.manage`, `U` |
| `POST /api/agents/{agent_id}/copy` | `agent.manage`, `U` |
| `DELETE /api/agents/{agent_id}` | `agent.manage`, `U` |

| MCP operation | Permission/target |
| --- | --- |
| `GET /api/mcp` | `mcp.manage`, `U` |
| `POST /api/mcp` | `mcp.manage`, `U` |
| `GET /api/mcp/{client_key}` | `mcp.manage`, `U` |
| `PUT /api/mcp/{client_key}` | `mcp.manage`, `U` |
| `PATCH /api/mcp/{client_key}/toggle` | `mcp.manage`, `U` |
| `DELETE /api/mcp/{client_key}` | `mcp.manage`, `U` |
| `GET /api/mcp/{client_key}/tools` | `mcp.manage`, `U` |
| `POST /api/mcp/{client_key}/tools/sync` | `mcp.manage + mcp.sync`, `U` |
| `PUT /api/mcp/{client_key}/tools` | `mcp.manage`, `U` |

| Tool operation | Permission/target |
| --- | --- |
| `GET /api/tools` | `tool.manage`, `U` |
| `GET /api/tools/{tool_id}` | `tool.manage`, `U` |
| `PATCH /api/tools/{tool_id}/toggle` | `tool.manage`, `U` |

| LLM Provider operation | Permission/target |
| --- | --- |
| `GET /api/models` | `model.manage`, `U` |
| `POST /api/models/custom-providers` | `model.manage`, `U` |
| `PUT /api/models/{provider_id}/config` | `model.manage`, `U` |
| `POST /api/models/{provider_id}/models` | `model.manage`, `U` |
| `PUT /api/models/{provider_id}/models/{model_id}/config` | `model.manage`, `U` |
| `DELETE /api/models/{provider_id}/models/{model_id}` | `model.manage`, `U` |
| `POST /api/models/{provider_id}/discover` | `model.manage`, `U` |
| `POST /api/models/{provider_id}/models/{model_id}/probe-multimodal` | `model.manage + model.run`, `U` |
| `POST /api/models/{provider_id}/test` | `model.manage + model.run`, `U` |
| `POST /api/models/{provider_id}/models/{model_id}/test` | `model.manage + model.run`, `U` |
| `GET /api/models/active` | `model.manage`, `U` |
| `PUT /api/models/active` | `model.manage`, `U` |

| Skill operation | Permission/target |
| --- | --- |
| `GET /api/skills` | `skill.manage`, `U` |
| `POST /api/skills` | `skill.manage`, `U` |
| `POST /api/skills/import` | `skill.manage`, `U` |
| `GET /api/skills/{skill_name}` | `skill.manage`, `U` |
| `PUT /api/skills/{skill_name}` | `skill.manage`, `U` |
| `PATCH /api/skills/{skill_name}/toggle` | `skill.manage`, `U` |
| `DELETE /api/skills/{skill_name}` | `skill.manage`, `U` |

| Platform/Audit operation | Permission/target |
| --- | --- |
| `GET /api/platform/overview` | `platform.read`, `U` |
| `GET /api/audit/events` | `audit.read`, grant-derived SQL scope |
| `GET /api/audit/events/{event_id}` | `audit.read`, `G/O` |
| `GET /api/audit/events/{event_id}/related` | `audit.read`, authorize anchor then related scope |
| `GET /api/health` | Explicitly public; no session or permission |

Audit routes use `require_scoped_permission("audit.read")`, not the default unit-target dependency. Tests prove project and own grants enter the route but SQL returns only their exact projects or actor-owned events; a detail outside that union and an unauthorized related-event anchor both return 404. The related query filters every returned event, not only its anchor.

Global configuration APIs are deliberately unit-admin surfaces in this phase. Menu filtering uses the same target kind, so project-scoped `agent.manage`, `agent.run`, `model.manage`, `skill.manage`, or `tool.manage` does not expose or invoke a unit-global resource. The resource-scope plan may open project-scoped read/run access only after adding database ownership/grant predicates.

The existing Chat view obtains Agent metadata from the global `/api/agents` endpoint, and the Dashboard reads the unit-global `/api/platform/overview`; therefore this foundation's non-admin acceptance scope is authentication, project selection, menus, and authorization errors, not end-to-end Chat or Dashboard data. `business-resource-authorization` must add a project-filtered executable-Agent catalogue and project-safe dashboard query before those pages are released to project roles. Do not weaken the global endpoints as a compatibility shortcut. `/api/models` is the current LLM Provider configuration API; the future water-model registry must use an unambiguous resource path.

- [ ] **Step 2: Run and verify failure**

Run:

```powershell
if (Test-Path Env:TEST_DATABASE_URL) {
    throw "Refusing unit tests with inherited TEST_DATABASE_URL; PostgreSQL tests must use backend/tests/support/run_postgres_tests.ps1"
}
$env:PYTHONPATH = "backend"
python -m pytest -q --ignore=backend/tests/integration backend/tests/test_agents.py backend/tests/test_mcp.py backend/tests/tools/test_api.py backend/tests/test_model_providers.py backend/tests/test_skills.py backend/tests/test_platform.py backend/tests/conversations/test_api.py backend/tests/audit/test_api.py
```

Expected: FAIL for unauthenticated Skills/platform routes and tests still coupled to development Headers.

- [ ] **Step 3: Apply dependencies without role-name checks**

Remove router-local `"admin"` and `project_admin` decisions. Attach permission dependencies at the narrowest route level. Project APIs use `require_permission(code, project_required=True)`; unit-global APIs use `require_permission(code)` and its trusted `U` target. `U+PC` conversation commands evaluate `require_project_context` first and only then unit-target `require_permission("agent.run")`, so a reconciled missing project deterministically returns 409 before any Agent or object resolution. Audit APIs use `require_scoped_permission("audit.read")` and mandatory Repository predicates. Keep `/api/health` outside authenticated router dependencies and assert that no other current business route is public.

Migrate only the listed HTTP routers, request-path services, management-audit helpers, and authorization-aware repositories from legacy `RequestContext` to `AuthorizationContext`. Unit-global management audit events write `project_id=None`, `event_scope="unit"`, and `authorization_scope="unit"`; request-accepted project runtime events read non-null `current_project_id`. No request-side `AuthorizationContext` consumer reads legacy `.project_id/.role/.roles`. Persisted asynchronous runtime code keeps `ToolExecutionContext.project_id`, run/conversation IDs, timezone, and actor-role snapshot. Existing scoped object lookups retain safe 404 behavior.

- [ ] **Step 4: Run focused and complete backend tests**

Run:

```powershell
if (Test-Path Env:TEST_DATABASE_URL) {
    throw "Refusing unit tests with inherited TEST_DATABASE_URL; PostgreSQL tests must use backend/tests/support/run_postgres_tests.ps1"
}
$env:PYTHONPATH = "backend"
python -m pytest -q --ignore=backend/tests/integration backend/tests/test_agents.py backend/tests/test_mcp.py backend/tests/tools/test_api.py backend/tests/test_model_providers.py backend/tests/test_skills.py backend/tests/test_platform.py backend/tests/conversations backend/tests/audit backend/tests/core/test_request_context.py
if ($LASTEXITCODE -ne 0) {
    throw "Focused Task 9 unit tests failed"
}
python -m pytest -q --ignore=backend/tests/integration backend/tests
if ($LASTEXITCODE -ne 0) {
    throw "Complete backend unit tests failed"
}
```

Expected: all backend unit tests PASS. `backend/tests/integration` is not collected in Task 9 and is never allowed to use an inherited database URL; PostgreSQL suites run only through the disposable runner in Tasks 2, 3, 7, and 12.

- [ ] **Step 5: Commit**

```powershell
git add -- backend/app/conversations/router.py backend/app/conversations/service.py backend/app/agents/router.py backend/app/agents/service.py backend/app/mcp/router.py backend/app/mcp/service.py backend/app/tools/router.py backend/app/tools/service.py backend/app/model_providers/router.py backend/app/model_providers/service.py backend/app/skills/router.py backend/app/platform/router.py backend/app/audit/router.py backend/app/audit/service.py backend/app/audit/policy.py backend/app/audit/repository.py backend/app/audit/management.py backend/app/core/request_context.py backend/tests/test_agents.py backend/tests/test_mcp.py backend/tests/tools/test_api.py backend/tests/test_model_providers.py backend/tests/test_skills.py backend/tests/test_platform.py backend/tests/conversations/test_api.py backend/tests/conversations/test_service.py backend/tests/audit/test_api.py backend/tests/audit/test_repository.py backend/tests/audit/test_service.py backend/tests/core/test_request_context.py
git diff --cached --name-only
git commit -m "feat: gate platform api with scoped permissions"
```

Expected before commit: the staged-name output is exactly the 30 Task 9 files above.

### Task 10: Replace Frontend Transport And Mock Session State

**Files:**

- Create: `frontend/src/api/auth.ts`
- Create: `frontend/src/api/auth.test.ts`
- Create: `frontend/src/stores/auth.ts`
- Create: `frontend/src/stores/auth.test.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/client.test.ts`
- Modify: `frontend/src/api/runEvents.ts`
- Modify: `frontend/src/api/runEvents.test.ts`
- Modify: `frontend/src/main.ts`
- Modify: `frontend/src/stores/conversations.ts`
- Modify: `frontend/src/stores/conversations.test.ts`
- Modify: `frontend/src/vite-env.d.ts`

**Interfaces:**

- Produces `authApi`, `useAuthStore`, centralized Cookie/CSRF/401/403/exact-context-409 transport hooks, one-flight authorization refresh, authenticated-request cancellation, and project-request cancellation; transport never replays a request after an auth or context handler.
- Task 11 consumes `AuthMe`, `AuthMenuNode`, and store actions; no frontend module reads OIDC Tokens or development identity Headers.

- [ ] **Step 1: Write failing API and store tests**

Define exact contracts:

```ts
export type AuthStatus =
  | 'idle'
  | 'bootstrapping'
  | 'anonymous'
  | 'authenticated'
  | 'refreshing-authorization'
  | 'switching-project';
export type MenuGroupKey = 'agents' | 'capabilities' | 'resources' | 'operations' | 'security' | 'system';
export type PermissionTarget = 'unit' | 'current_project';
export type AuthPermission = { code: string; target: PermissionTarget };


export type AuthMenuRouteNode = { kind: 'route'; key: string; route_key: string; title: string };
export type AuthMenuGroupNode = { kind: 'group'; key: MenuGroupKey; title: string; children: AuthMenuRouteNode[] };
export type AuthMenuNode = AuthMenuGroupNode | AuthMenuRouteNode;

export interface AuthMe {
  user: { id: string; display_name: string };
  unit: { id: string; name: string };
  current_project: { id: string; name: string } | null;
  projects: Array<{ id: string; name: string }>;
  roles: string[];
  permissions: AuthPermission[];
  menus: AuthMenuNode[];
  authorization_version: number;
  csrf_token: string;
  session: { idle_expires_at: string; absolute_expires_at: string };
}
```

The store exposes `hasCapability(code: string, target: PermissionTarget): boolean` and compares one complete pair. Tests prove a project-target `agent.run` does not satisfy `hasCapability('agent.run', 'unit')`, a unit capability does, duplicates are rejected or normalized deterministically, and project-target capabilities disappear after switching to `current_project=null`.

Test `bootstrap()`, `refresh()`, `switchProject(id)`, `logout()`, one-shot `handleUnauthorized()`, `handleForbidden()`, `handleContextChanged()`, their shared authorization-refresh single flight, malformed menu rejection, and `clearSession()`. Assert localStorage/sessionStorage contain no auth data.

Transport tests assert:

- `credentials: 'same-origin'` is explicit.
- Unsafe methods inject `X-CSRF-Token`; safe methods do not.
- A 204 response does not attempt JSON parsing.
- Errors parse `{code,message,request_id}` with legacy `detail` fallback during migration; `ApiError` exposes stable `status`, `code`, and `requestId`.
- 401 triggers one centralized cleanup despite concurrent failures.
- Every parsed 403 invokes the forbidden handler, preserves the login, and rejects the original request after refresh; concurrent 403 responses share one `authApi.me()` call.
- Only `409` with parsed code `AUTH_CONTEXT_CHANGED` invokes the context handler; unrelated 409 conflicts do not.
- Concurrent ordinary requests and `runEvents` responses across 403 and exact context 409 share the same one-flight authorization refresh.
- Before refresh, synchronously abort all authenticated HTTP/SSE requests, call `conversationStore.clearProjectState()`, set status to `refreshing-authorization`, and replace current permissions/menus with empty arrays while preserving identity and session data. Task 11 must dispose dynamic routes in that same synchronous turn.
- The one internal `authApi.me()` request bypasses 403/409 recovery hooks but still uses centralized 401 handling. Its response atomically replaces the empty snapshot; a returned null project keeps project capabilities and menus absent.
- Every original request still rejects with its original parsed `ApiError` after refresh. No GET, SSE fetch, POST, PUT, PATCH, or DELETE is automatically replayed; an unsafe request's fetch call count remains exactly one.
- If refresh returns 401, the existing one-flight unauthorized path clears the session. A 403 or exact context 409 itself never logs out; a network failure leaves the fail-closed empty authorization snapshot and exposes a retryable refresh error rather than restoring stale grants.
- Project switch aborts all old project-scoped requests.
- `runEvents.ts` shares Cookie, 401, 403, exact-context-409 handling, cancellation, and the no-replay rule, and sends no identity Header.

- [ ] **Step 2: Run and verify failure**

Run:

```powershell
npm --prefix frontend test -- src/api/client.test.ts src/api/runEvents.test.ts src/api/auth.test.ts src/stores/auth.test.ts src/stores/conversations.test.ts
```

Expected: FAIL because the transport has no auth hooks and the store still generates Mock Tokens.

- [ ] **Step 3: Implement transport hooks and auth API**

Expose:

```ts
export interface RequestOptions {
  timeoutMs?: number;
  scope?: 'global' | 'project';
  authRecovery?: 'default' | 'skip-authorization-refresh';
}

export interface AuthTransportHooks {
  getCsrfToken(): string | null;
  onUnauthorized(): Promise<void>;
  onForbidden(): Promise<void>;
  onContextChanged(): Promise<void>;
}

export function configureAuthTransport(hooks: AuthTransportHooks): void;
export function abortProjectRequests(): void;
export function abortAuthenticatedRequests(): void;
export function request<T>(
  path: string,
  init?: RequestInit,
  options?: RequestOptions,
): Promise<T>;
```

`authApi.loginUrl(returnTo)` accepts only a site-relative route and returns `/api/auth/login?return_to=...`. `me`, `switchProject`, and `logout` use the shared client.

Parse `{code,message,request_id}` before dispatching hooks. For every 403, await `onForbidden()`; for `409/AUTH_CONTEXT_CHANGED`, await `onContextChanged()`; then throw the original `ApiError` without calling `request()` or `fetch()` again. Both hooks join the same store single flight. All other 409 responses bypass recovery. The internal refresh uses `authRecovery: 'skip-authorization-refresh'` to prevent recursion while retaining 401 handling. `runEvents.ts` uses the same response-error dispatcher.

- [ ] **Step 4: Implement in-memory auth state and startup bootstrap**

`useAuthStore` stores only the current `AuthMe` and status. `main.ts` creates Pinia, configures transport hooks, awaits `authStore.bootstrap()`, installs the router, waits for readiness, and mounts. A network failure is distinct from anonymous 401 and renders a retryable startup state.

Project switch order is fixed: abort old requests, clear conversations and project caches, call the backend, replace `AuthMe`, then reinstall routes in Task 11.

`useAuthStore.handleForbidden()` and `handleContextChanged()` delegate to one module-private `refreshAuthorizationState()` Promise, independently of `handleUnauthorized()`. Its first caller aborts authenticated HTTP/SSE, clears Conversation state (increment poll token; clear conversations, active ID, messages, active run, events, and tool activities), publishes a fail-closed copy of `AuthMe` with empty permissions/menus and `refreshing-authorization`, then calls global-scope `authApi.me()` exactly once with authorization recovery disabled and atomically replaces `AuthMe`. Concurrent 403/409 callers await the same Promise. It preserves identity/session data and never retries the failed command. Task 11 synchronously disposes routes on the empty snapshot, then reinstalls from the replacement and routes a null project to selection or 403; no Promise is persisted in Pinia state.

- [ ] **Step 5: Run tests and commit**

Run:

```powershell
npm --prefix frontend test -- src/api/client.test.ts src/api/runEvents.test.ts src/api/auth.test.ts src/stores/auth.test.ts src/stores/conversations.test.ts
if ($LASTEXITCODE -ne 0) {
    throw "Task 10 frontend tests failed"
}
npm --prefix frontend run build
if ($LASTEXITCODE -ne 0) { throw "Task 10 frontend build failed" }
```

Expected: focused Vitest tests PASS and the TypeScript production build succeeds.

```powershell
git add -- frontend/src/api/auth.ts frontend/src/api/auth.test.ts frontend/src/stores/auth.ts frontend/src/stores/auth.test.ts frontend/src/api/client.ts frontend/src/api/client.test.ts frontend/src/api/runEvents.ts frontend/src/api/runEvents.test.ts frontend/src/main.ts frontend/src/stores/conversations.ts frontend/src/stores/conversations.test.ts frontend/src/vite-env.d.ts
git diff --cached --name-only
git commit -m "feat: add cookie auth frontend state"
```

Expected before commit: the staged-name output is exactly the 12 Task 10 files above.

### Task 11: Replace Login, Routing, Menus, Project Context, And Layout State

**Files:**

- Create: `frontend/src/router/routeRegistry.ts`
- Create: `frontend/src/router/dynamicRoutes.ts`
- Create: `frontend/src/router/dynamicRoutes.test.ts`
- Create: `frontend/src/router/index.test.ts`
- Create: `frontend/src/views/auth/LoginView.test.ts`
- Create: `frontend/src/views/auth/ProjectSelectionView.vue`
- Create: `frontend/src/views/auth/ProjectSelectionView.test.ts`
- Create: `frontend/src/views/errors/ForbiddenView.vue`
- Create: `frontend/src/views/errors/NotFoundView.vue`
- Create: `frontend/src/layouts/AppLayout.test.ts`
- Create: `frontend/src/views/resources/ResourceListView.test.ts`
- Modify: `frontend/src/router/index.ts`
- Modify: `frontend/src/router/routes.ts`
- Modify: `frontend/src/views/auth/LoginView.vue`
- Modify: `frontend/src/layouts/AppLayout.vue`
- Modify: `frontend/src/views/security/AuditLogView.vue`
- Modify: `frontend/src/views/security/AuditLogView.test.ts`
- Modify: `frontend/src/views/resources/ResourceListView.vue`
- Modify: `frontend/src/views/tools/ToolManageView.test.ts`
- Modify: `frontend/src/views/runs/AgentRunListView.test.ts`
- Delete: `frontend/src/stores/permission.ts`

**Interfaces:**

- Produces `installAuthorizedRoutes(router, menus, auth: Pick<AuthMe, 'current_project' | 'permissions'>) -> () => void`, `MenuConfigurationError`, and a static `routeRegistry` keyed by backend `route_key`.
- Every registry entry fixes `meta.permissionCode`, `meta.permissionTarget`, and `meta.projectRequired` locally. All current-project routes and the unit-target `chat` route require a project; other unit routes do not. Server data cannot override these fields.
- Consumes `useAuthStore`; menu visibility and route installation never derive from local role names. A synchronous watcher disposes all dynamic routes when status becomes `refreshing-authorization` or menus are cleared. Fixed `/chat/focus` independently fixes `agent.run/unit` plus `projectRequired=true`.

- [ ] **Step 1: Write failing routing and UI behavior tests**

Test exact cases:

- Anonymous business navigation redirects to `/login?redirect=<encoded relative route>`.
- Authenticated navigation to `/login` enters the first authorized unit route, otherwise a project route when selected, otherwise project selection when `projects` is non-empty; it never assumes Dashboard.
- `current_project=null` still permits authorized unit routes, while a project-target route goes to `/select-project` without auto-selecting the first project.
- When a selected project has no authorized route, root navigation goes to `/403`. With `current_project=null`, an empty unit menu goes to `/select-project` only when the server returned at least one selectable project; no project and no route goes to `/403`.
- An unknown group/route key, duplicate path, malformed node, or fixed-route override rejects the entire menu before any route is installed and shows `AUTH_MENU_CONFIGURATION_INVALID`.
- A missing permission routes to `/403` without clearing login.
- Project switch removes old dynamic routes and installs only the new menu tree.
- A 403 or exact context 409 preserves login but synchronously removes navigation and every dynamic route before the one-flight `/auth/me` refresh resolves; caches and requests are cleared, the original request is not replayed, and only the replacement menu tree is installed afterward.
- Fixed `/login`, `/select-project`, `/403`, `/404`, `/chat/focus`, both legacy aliases, root shell, and catch-all routes survive dynamic install, disposal, and project switch.
- All six group keys and all 36 route keys parse; every path, component, permission code, local `permissionTarget`, and local `projectRequired === (permissionTarget === 'current_project' || routeKey === 'chat')` value equals the contract exactly. A server-supplied `projectRequired` field is malformed and cannot override the local value.
- With `current_project=null`, `router.hasRoute('chat')` is false; if a malformed menu still includes Chat, reject the entire menu as `AUTH_MENU_CONFIGURATION_INVALID` before installing any leaf.
- Direct `/chat` and `/chat/focus` navigation with no current project enters `/select-project?redirect=<encoded safe original route>` when projects are selectable, otherwise `/403`.
- With a selected project but no `agent.run/unit`, or only `agent.run/current_project`, both Chat addresses enter `/403` without clearing the session.
- With a selected project and `agent.run/unit`, `/chat` enters only when the dynamic Chat leaf is installed, otherwise `/403`; `/chat/focus` may enter without a menu leaf.
- Every Chat rejection or selection branch proves the literal lazy loader is not resolved, `AgentConsoleView` is not mounted, and conversation/Agent APIs are not called.
- Switching to `current_project=null` disposes dynamic Chat before routing the old Chat location to selection or 403.
- Legacy `/tenant/resources` and `/system/tenant-projects` URLs redirect only when their dynamic target is installed; otherwise they enter `/403`, not catch-all `/404`.
- Catch-all distinguishes 404 from 403 and does not silently redirect to dashboard.
- Login page has no username, password, remember, or role controls.
- Login button navigates to the BFF URL while preserving a safe `return_to`.
- AppLayout shows `/auth/me` user, role labels, and current project; no role switch remains.
- Audit page capabilities use permission codes, not `VITE_DEV_USER_ROLES`.

- Resource review/manage buttons use exact scoped capabilities rather than `isAdmin`.
- Existing Tool, Run, and Audit tests no longer import raw `permission.ts`, assert legacy static-route source, or stub `VITE_DEV_USER_ROLES`.
- [ ] **Step 2: Run and verify failure**

Run:

```powershell
npm --prefix frontend test -- src/router/dynamicRoutes.test.ts src/router/index.test.ts src/views/auth/LoginView.test.ts src/views/auth/ProjectSelectionView.test.ts src/layouts/AppLayout.test.ts src/views/resources/ResourceListView.test.ts src/views/tools/ToolManageView.test.ts src/views/runs/AgentRunListView.test.ts src/views/security/AuditLogView.test.ts
```

Expected: FAIL because routes and layout still depend on `permission.ts`.

- [ ] **Step 3: Implement fixed route registry and dynamic installation**

Implement every row in the Route And Menu Contract near the top of this plan. Each registry entry uses a literal lazy import and its listed path; GenericModule and ResourceList entries also set their existing static `meta.module` locally. Store the listed permission code, target, and project requirement locally. The server supplies only node kind, allowlisted keys, labels, nesting, and order.

Register fixed routes and two legacy aliases statically before installing server-derived routes. `/chat` is never a fixed route and is added only after `AuthMe` exists, `current_project` is non-null, the menu contains Chat, and exact `agent.run/unit` is present. `/chat/focus` is fixed but retains a literal lazy import; the global guard completes project and capability checks before Vue resolves the component.

Guard order is anonymous to Login, then known project-required path, then exact capability, then dynamic-route presence. For `/chat` or `/chat/focus`, null project plus selectable projects enters `/select-project` with a safe relative redirect; null project with no selectable project enters `/403`; selected project with insufficient capability enters `/403`. The local registry identifies an uninstalled `/chat` before catch-all: a selected and capable user without the dynamic leaf enters `/403`, while only truly unknown paths enter `/404`. Each legacy alias checks that its target route is installed before redirecting and otherwise enters `/403`.

```ts
export type RegisteredRouteKey = keyof typeof routeRegistry;

export function isRegisteredRouteKey(value: string): value is RegisteredRouteKey {
  return Object.prototype.hasOwnProperty.call(routeRegistry, value);
}
```

`installAuthorizedRoutes` validates the complete tree, group keys, route keys, unique names/paths, fixed-route exclusions, exact capabilities, and local project requirements before calling `router.addRoute()`. A server-supplied authorization metadata field or a project-required leaf while `current_project=null` is invalid. Any violation throws `MenuConfigurationError`; the caller disposes the previous dynamic tree, preserves the session, and enters fixed `/403` with the stable configuration error code.

When Task 10 publishes the empty authorization snapshot for a 403 or `AUTH_CONTEXT_CHANGED`, a `flush: 'sync'` watcher disposes the old dynamic tree before control returns to transport. After the one-flight `AuthMe` replacement, validate and reinstall only the new tree, then route a null project to selection or 403. Never replay or remount the denied command.

After validation, record every `router.addRoute()` disposer and return one idempotent disposer that removes only dynamic routes. Root navigation skips every candidate whose exact capability or local project requirement is not satisfied, then selects the first authorized unit route in server order; if none exists, it selects the first eligible project route only when `current_project` is non-null, sends a user with `current_project=null` and non-empty `projects` to `/select-project`, and sends a selected project with no route or a user with no project and no route to `/403`. It never assumes `/dashboard`.

Store each route's listed code, target, and project requirement locally in `routeRegistry` as `meta.permissionCode`, `meta.permissionTarget`, and `meta.projectRequired`; never accept them from the server. `ResourceListView` replaces every `isAdmin` branch with exact pairs: publish commands require `resource.publish` at the current route target, create/edit/manage/down commands require `resource.manage` at that target, and review commands require `resource.review/current_project`. View/copy remains covered by the installed `resource.read` route.

`AuditLogView` removes development-role parsing. `canFilterProject` requires `audit.read/unit`; `canFilterUser` accepts either `audit.read/unit` or `audit.read/current_project`. These controls are only presentation: every query remains constrained by the backend's grant-derived SQL, including callers with an `own` grant.

- [ ] **Step 4: Preserve the existing visual shell**

Keep the current LoginView brand, capability section, spacing, breakpoints, colors, and 8px radii. Replace only the Mock form with a single “统一身份登录” command, Provider-unavailable error state, and retry. Do not redesign global CSS.

Keep AppLayout navigation hierarchy, icons, mobile drawer, and density. Replace hard-coded user/project and role-switch UI with `AuthMe` data, an accessible project menu, and logout.

- [ ] **Step 5: Run frontend suite and build**

Run:

```powershell
npm --prefix frontend test
npm --prefix frontend run build
```

Expected: all Vitest tests PASS, TypeScript check passes through `npm run build`, and the production bundle contains no `mock-token`, default password, role switch, or `VITE_DEV_*` identity values.

- [ ] **Step 6: Commit**

```powershell
git add -- frontend/src/router/routeRegistry.ts frontend/src/router/dynamicRoutes.ts frontend/src/router/dynamicRoutes.test.ts frontend/src/router/index.test.ts frontend/src/views/auth/LoginView.test.ts frontend/src/views/auth/ProjectSelectionView.vue frontend/src/views/auth/ProjectSelectionView.test.ts frontend/src/views/errors/ForbiddenView.vue frontend/src/views/errors/NotFoundView.vue frontend/src/layouts/AppLayout.test.ts frontend/src/views/resources/ResourceListView.test.ts frontend/src/router/index.ts frontend/src/router/routes.ts frontend/src/views/auth/LoginView.vue frontend/src/layouts/AppLayout.vue frontend/src/views/security/AuditLogView.vue frontend/src/views/security/AuditLogView.test.ts frontend/src/views/resources/ResourceListView.vue frontend/src/views/tools/ToolManageView.test.ts frontend/src/views/runs/AgentRunListView.test.ts frontend/src/stores/permission.ts
git diff --cached --name-only
git commit -m "feat: replace mock login with oidc session ui"
```

Expected before commit: the staged-name output is exactly the 21 Task 11 files above, including the deletion of `frontend/src/stores/permission.ts`.

### Task 12: Remove Production Dev Identity, Harden Deployment Defaults, Document, And Verify

**Files:**

- Modify: `frontend/Dockerfile`
- Modify: `backend/Dockerfile`
- Create: `backend/app/identity/client_address.py`
- Modify: `backend/app/identity/middleware.py`
- Create: `backend/tests/identity/test_client_address.py`
- Modify: `frontend/nginx.conf`
- Modify: `compose.yaml`
- Modify: `.env.example`
- Modify: `backend/tests/test_frontend_dev_identity_config.py`
- Create: `backend/tests/security/test_deployment_security.py`
- Create: `docs/deployment/oidc-development.md`
- Modify: `README.md`
- Modify: `backend/README.md`
- Modify: `frontend/README.md`
- Modify: `docs/智能体平台详细功能设计与现状改造清单.md`
- Modify: `docs/deployment/aliyun-ecs-http.md`

**Interfaces:**

- Produces a reproducible development and CI procedure for PostgreSQL + Mock OIDC plus fail-closed client-address, proxy, database-port, and HTTP-deployment defaults.
- Leaves Keycloak, emergency access, real Provider adaptation, and business resource ownership behind explicit production gates rather than implying completion; the client-address resolver is complete now and is reused by later emergency access.

- [ ] **Step 1: Write failing deployment-security tests**

Create `backend/tests/security/test_deployment_security.py` with these exact repository-level assertions:

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_nginx_discards_external_forwarding_headers():
    nginx = (ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")

    assert 'proxy_set_header Forwarded "";' in nginx
    assert 'proxy_set_header X-Forwarded-For $remote_addr;' in nginx
    assert 'proxy_set_header X-Real-IP $remote_addr;' in nginx
    assert 'proxy_set_header X-Forwarded-Proto $scheme;' in nginx
    assert 'proxy_set_header X-Forwarded-Host "";' in nginx
    assert 'proxy_set_header X-Forwarded-Port "";' in nginx
    assert "$proxy_add_x_forwarded_for" not in nginx


def test_api_disables_uvicorn_proxy_header_rewriting():
    dockerfile = (ROOT / "backend/Dockerfile").read_text(encoding="utf-8")

    assert "--no-proxy-headers" in dockerfile
    assert " --proxy-headers" not in dockerfile


def test_postgres_is_only_published_on_ipv4_loopback():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert '"127.0.0.1:${POSTGRES_PORT:-5432}:5432"' in compose
    assert '      - "5432:5432"' not in compose
    assert 'POSTGRES_DB: "${POSTGRES_DB:-iap}"' in compose
    assert 'POSTGRES_USER: "${POSTGRES_USER:-iap}"' in compose
    assert 'POSTGRES_PASSWORD: "${POSTGRES_PASSWORD:-iap}"' in compose
    assert 'postgresql+psycopg://${POSTGRES_USER:-iap}:${POSTGRES_PASSWORD:-iap}@postgres:5432/${POSTGRES_DB:-iap}' in compose
    assert 'test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]' in compose
    assert "pg_isready -U iap -d iap" not in compose


def test_http_deployment_is_explicitly_forbidden_for_oidc_production():
    guide = (
        ROOT / "docs/deployment/aliyun-ecs-http.md"
    ).read_text(encoding="utf-8")

    assert "不得用于 OIDC 生产" in guide
    assert "OIDC 生产必须使用域名和 HTTPS" in guide
    assert "HTTP 仅允许回环地址测试" in guide
```

Before changing Docker or Compose, reverse `backend/tests/test_frontend_dev_identity_config.py` so the test itself fails against the current repository:

```python
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
IDENTITY_VARIABLES = (
    "VITE_DEV_UNIT_ID",
    "VITE_DEV_USER_ID",
    "VITE_DEV_PROJECT_ID",
    "VITE_DEV_USER_ROLES",
    "VITE_DEV_USER_ROLE",
)


def test_production_frontend_build_inputs_exclude_development_identity():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    build_args = compose["services"]["web"]["build"].get("args", {})
    dockerfile = (ROOT / "frontend/Dockerfile").read_text(encoding="utf-8")

    for variable in IDENTITY_VARIABLES:
        assert variable not in build_args
        assert variable not in dockerfile
```

Create `backend/tests/identity/test_client_address.py` with a small ASGI `Request` factory and these exact cases:

```python
def test_untrusted_peer_cannot_forge_any_forwarding_header():
    request = make_request(
        "203.0.113.10",
        [
            ("forwarded", "for=198.51.100.7"),
            ("x-forwarded-for", "198.51.100.7"),
            ("x-real-ip", "198.51.100.7"),
        ],
    )
    assert resolve_client_address(request, ("172.18.0.0/16",)) == "203.0.113.10"


def test_trusted_peer_accepts_exactly_one_valid_xff():
    request = make_request(
        "172.18.0.4",
        [("x-forwarded-for", "198.51.100.7")],
    )
    assert resolve_client_address(request, ("172.18.0.0/16",)) == "198.51.100.7"


@pytest.mark.parametrize(
    "headers",
    [
        [("x-forwarded-for", "198.51.100.7, 10.0.0.2")],
        [
            ("x-forwarded-for", "198.51.100.7"),
            ("x-forwarded-for", "10.0.0.2"),
        ],
        [("x-forwarded-for", "not-an-ip")],
    ],
)
def test_trusted_peer_rejects_ambiguous_or_invalid_xff(headers):
    request = make_request("172.18.0.4", headers)
    assert resolve_client_address(request, ("172.18.0.0/16",)) == "172.18.0.4"
```

Run:

```powershell
$env:PYTHONPATH = "backend"
python -m pytest -q backend/tests/security/test_deployment_security.py backend/tests/test_frontend_dev_identity_config.py backend/tests/identity/test_client_address.py
```

Expected: FAIL for the reversed frontend test, missing client-address resolver, current appended XFF, Uvicorn proxy mode, all-interface PostgreSQL port, hard-coded PostgreSQL healthcheck, unparameterized acceptance database, and HTTP guide.


- [ ] **Step 2: Harden frontend identity, proxy headers, and database exposure**

Remove `VITE_DEV_UNIT_ID`, `VITE_DEV_USER_ID`, `VITE_DEV_PROJECT_ID`, `VITE_DEV_USER_ROLES`, and `VITE_DEV_USER_ROLE` from `frontend/Dockerfile` and Web build arguments in `compose.yaml`. Keep backend development identity Headers only in the isolated loopback adapter documented in Step 3. The reversed static test from Step 1 must pass before any production bundle is built; the built bundle is scanned only after `npm run build` in Steps 4 and 5.

Parameterize the disposable database while retaining local defaults:

```yaml
postgres:
  environment:
    POSTGRES_DB: "${POSTGRES_DB:-iap}"
    POSTGRES_USER: "${POSTGRES_USER:-iap}"
    POSTGRES_PASSWORD: "${POSTGRES_PASSWORD:-iap}"
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
  ports:
    - "127.0.0.1:${POSTGRES_PORT:-5432}:5432"

api:
  environment:
    DATABASE_URL: "postgresql+psycopg://${POSTGRES_USER:-iap}:${POSTGRES_PASSWORD:-iap}@postgres:5432/${POSTGRES_DB:-iap}"
```

Document all four variables in `.env.example` with development-only defaults. Acceptance uses a GUID password containing only URI-safe hex. Container-to-container access remains `postgres:5432`; a production override omits the host port and injects the password through the deployment secret mechanism.

Replace the Nginx proxy-header block with these values so an external client cannot extend a trusted forwarding chain:

```nginx
proxy_set_header Host $host;
proxy_set_header Forwarded "";
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-Host "";
proxy_set_header X-Forwarded-Port "";
```

Set the backend Docker command to `--no-proxy-headers`; deleting the current flag is insufficient because Uvicorn enables proxy-header rewriting by default. The application derives OIDC redirects from validated configuration, not request Host or forwarding headers.

Implement this exact application boundary:

```python
def resolve_client_address(
    request: Request,
    trusted_proxy_cidrs: Sequence[str],
) -> str:
    """Return a canonical client IP without trusting an arbitrary header chain."""
```

Parse the direct peer with `ipaddress.ip_address`. If it is outside every configured trusted proxy network, return it and ignore `Forwarded`, every `X-Forwarded-For`, and `X-Real-IP`. For a trusted direct peer, inspect the raw ASGI Header list and accept XFF only when there is exactly one Header, its value contains no comma, and it parses as one IP; otherwise return the direct peer. Never consult `Forwarded` or `X-Real-IP`. Authentication audit metadata uses this resolver, but the development identity adapter continues requiring the direct peer itself to be loopback. The later emergency plan must reuse this resolver and must not re-enable generic Uvicorn proxy trust.

Run:

```powershell
$env:PYTHONPATH = "backend"
python -m pytest -q backend/tests/security/test_deployment_security.py backend/tests/test_frontend_dev_identity_config.py backend/tests/identity/test_client_address.py
```

Expected: PASS.

- [ ] **Step 3: Document exact local flow and limitations**

`docs/deployment/oidc-development.md` must document:

1. Start disposable PostgreSQL.
2. Run Alembic to head.
3. Start the protocol-level Mock Provider with generated ephemeral keys.
4. Bootstrap one test unit, two projects, and one exact Mock issuer/subject binding.
5. Start API with development/test auth keys supplied through temporary files.
6. Start the Vite frontend.
7. Exercise login, explicit project selection, refresh, project switch, 403, and logout.
8. Confirm no OIDC Token appears in Storage, URLs, logs, or API responses.
9. State that production remains blocked by the four gates in this plan.

Do not include a reusable secret, real customer identifier, or production credential.

Update existing documentation with these exact replacements:

| File | Required replacement |
| --- | --- |
| `README.md` | Remove both PowerShell blocks that set `VITE_DEV_USER_ID/VITE_DEV_PROJECT_ID`, remove the paragraph that recommends browser identity Headers or Vite identity variables, and replace them with links to `docs/deployment/oidc-development.md` plus a statement that production Web authentication is opaque Cookie OIDC BFF only. |
| `backend/README.md` | Remove the Compose/Vite development-identity paragraph. Keep the Header adapter only under an “isolated backend tests” subsection requiring `IAP_ENVIRONMENT=test`, `IAP_ALLOW_DEV_IDENTITY=true`, and a direct loopback peer; state that it is never used by the frontend build. |
| `frontend/README.md` | Delete the `VITE_DEV_*` identity paragraph and replace “权限码 Mock” with `/api/auth/me` scoped capabilities and server-filtered route keys. |
| `docs/智能体平台详细功能设计与现状改造清单.md` | Replace the Mock Token/login status, browser Access/Refresh Token design, `/api/auth/refresh`, and P0 “current tenant/project” wording with the authoritative OIDC BFF, opaque Cookie, nullable unit/project context, and scoped-capability contract; link to the design and this plan. |

After the edits, these four files contain no instruction to build `VITE_DEV_*` identity, no demo-password or Mock-login path, and no claim that OIDC Tokens belong in browser storage. References to unrelated Mock business pages remain accurate and are not removed.


`docs/deployment/aliyun-ecs-http.md` begins with this release-blocking warning:

```markdown
> [!WARNING]
> 本文仅保留当前未启用 OIDC 的原型 HTTP 部署说明，不得用于 OIDC 生产。OIDC 生产必须使用域名和 HTTPS；公网 IP/HTTP 无法满足 Secure `__Host-iap_session` Cookie 和非回环 Redirect URI 要求。HTTP 仅允许回环地址测试。
```

Replace its advisory HTTPS language with mandatory limits:

```markdown
- 本文 HTTP 拓扑只能用于未启用 OIDC 的临时原型。
- OIDC 生产必须先配置域名和 HTTPS，并将 HTTP 重定向到 HTTPS；未完成时不得发布。
- OIDC 的非安全 Cookie 仅允许显式 `development/test` 环境和回环地址，不能作为生产降级开关。
```

- [ ] **Step 4: Run complete automated verification**

Run the unit suites without collecting integration tests, then build:

```powershell
if ($null -ne [System.Environment]::GetEnvironmentVariable("TEST_DATABASE_URL", [System.EnvironmentVariableTarget]::Process)) {
    throw "Refusing inherited TEST_DATABASE_URL for unit tests"
}
$env:PYTHONPATH = "backend"
python -m pytest -q backend/tests --ignore=backend/tests/integration
if ($LASTEXITCODE -ne 0) { throw "Backend unit tests failed" }
npm --prefix frontend test
if ($LASTEXITCODE -ne 0) { throw "Frontend tests failed" }
npm --prefix frontend run build
if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" }
docker compose config --quiet
if ($LASTEXITCODE -ne 0) { throw "Compose validation failed" }
docker compose build api web
if ($LASTEXITCODE -ne 0) { throw "Container image build failed" }
```

Then run PostgreSQL integration, binding, and Nginx syntax acceptance in a per-run Compose project. This procedure creates `iap_auth_test` through the PostgreSQL image entrypoint rather than assuming it already exists:

```powershell
$iapAcceptanceProject = "iap-auth-acceptance-$([guid]::NewGuid().ToString('N'))"
if ($iapAcceptanceProject -notmatch '^iap-auth-acceptance-[0-9a-f]{32}$') {
    throw "Unsafe acceptance project name: $iapAcceptanceProject"
}

$env:COMPOSE_PROJECT_NAME = $iapAcceptanceProject
$env:POSTGRES_DB = "iap_auth_test"
$env:POSTGRES_USER = "iap_auth_test"
$env:POSTGRES_PASSWORD = [guid]::NewGuid().ToString("N")
$env:POSTGRES_PORT = "0"
function Get-IapComposeProjectResources {
    param([Parameter(Mandatory)][string]$ProjectName)

    $iapResources = [System.Collections.Generic.List[string]]::new()
    $iapContainerIds = @(docker ps -aq --filter "label=com.docker.compose.project=$ProjectName")
    if ($LASTEXITCODE -ne 0) { throw "Failed to enumerate Compose containers" }
    foreach ($iapId in $iapContainerIds) {
        if (-not [string]::IsNullOrWhiteSpace($iapId)) {
            [void]$iapResources.Add("container:$iapId")
        }
    }

    $iapVolumeNames = @(docker volume ls -q --filter "label=com.docker.compose.project=$ProjectName")
    if ($LASTEXITCODE -ne 0) { throw "Failed to enumerate Compose volumes" }
    foreach ($iapName in $iapVolumeNames) {
        if (-not [string]::IsNullOrWhiteSpace($iapName)) {
            [void]$iapResources.Add("volume:$iapName")
        }
    }

    $iapNetworkIds = @(docker network ls -q --filter "label=com.docker.compose.project=$ProjectName")
    if ($LASTEXITCODE -ne 0) { throw "Failed to enumerate Compose networks" }
    foreach ($iapId in $iapNetworkIds) {
        if (-not [string]::IsNullOrWhiteSpace($iapId)) {
            [void]$iapResources.Add("network:$iapId")
        }
    }

    return $iapResources.ToArray()
}

$iapDisposableVerified = $false

try {
    $iapExistingResources = @(Get-IapComposeProjectResources -ProjectName $iapAcceptanceProject)
    if (@($iapExistingResources).Count -ne 0) {
        throw "Acceptance project already owns resources; refusing to reuse it"
    }
    $iapDisposableVerified = $true

    docker compose -p $iapAcceptanceProject up -d postgres
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL startup failed" }

    $iapPostgresReady = $false
    for ($iapAttempt = 0; $iapAttempt -lt 60; $iapAttempt++) {
        docker compose -p $iapAcceptanceProject exec -T postgres pg_isready -U $env:POSTGRES_USER -d $env:POSTGRES_DB *> $null
        if ($LASTEXITCODE -eq 0) {
            $iapPostgresReady = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $iapPostgresReady) { throw "PostgreSQL did not become ready" }

    $iapPostgresBinding = (
        docker compose -p $iapAcceptanceProject port postgres 5432
    ).Trim()
    if ($iapPostgresBinding -notmatch '^127\.0\.0\.1:(\d+)$') {
        throw "PostgreSQL is not bound only to IPv4 loopback: $iapPostgresBinding"
    }
    $iapPostgresPort = $Matches[1]

    $env:PYTHONPATH = "backend"
    $env:TEST_DATABASE_URL = (
        "postgresql+psycopg://{0}:{1}@127.0.0.1:{2}/{3}" -f
        $env:POSTGRES_USER,
        $env:POSTGRES_PASSWORD,
        $iapPostgresPort,
        $env:POSTGRES_DB
    )
    python -m pytest -q backend/tests/integration
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL integration tests failed" }

    docker compose -p $iapAcceptanceProject run --rm --no-deps --add-host api:127.0.0.1 web nginx -t
    if ($LASTEXITCODE -ne 0) { throw "Nginx syntax validation failed" }
} finally {
    if ($iapDisposableVerified) {
        if ($env:COMPOSE_PROJECT_NAME -ne $iapAcceptanceProject) {
            throw "Compose project changed; refusing cleanup"
        }
        docker compose -p $iapAcceptanceProject down -v --remove-orphans
        $iapDownExit = $LASTEXITCODE
        $iapRemainingResources = @(Get-IapComposeProjectResources -ProjectName $iapAcceptanceProject)
        if ($iapDownExit -ne 0) {
            throw "Compose cleanup failed with exit code $iapDownExit"
        }
        if (@($iapRemainingResources).Count -ne 0) {
            $iapRemainingText = $iapRemainingResources -join ', '
            throw "Compose cleanup left labeled resources: $iapRemainingText"
        }
    }
}
```

The GUID project must have zero labeled containers, volumes, and networks before startup. Each Docker enumeration command must succeed. Cleanup is permitted only after that preflight marks this exact project disposable; `down` must exit zero and a second three-kind enumeration must return zero resources. No fixed project name, existing volume, or unrelated Compose project is removed.

Expected: all suites PASS, PostgreSQL integration has no skip, both images build, Compose validation exits 0, Nginx reports valid syntax using the explicit temporary `api` host mapping, `iap_auth_test` exists, PostgreSQL reports only a random `127.0.0.1:<port>` binding, and cleanup leaves zero labeled containers, volumes, or networks.

- [ ] **Step 5: Run security and hygiene checks**

Every negative scan must fail on a match, treat exit code 1 as clean, and fail on any other ripgrep error:

```powershell
$iapRgPath = (Get-Command rg -CommandType Application -ErrorAction Stop).Source

function Assert-NoRgMatch {
    param(
        [string]$Pattern,
        [string[]]$Paths
    )

    & $iapRgPath -n -- $Pattern @Paths
    $iapRgExit = $LASTEXITCODE
    if ($iapRgExit -eq 0) {
        throw "Forbidden pattern matched: $Pattern"
    }
    if ($iapRgExit -ne 1) {
        throw "ripgrep failed with exit code $iapRgExit"
    }
}

Assert-NoRgMatch -Pattern 'mock-token|admin/123456|任意密码|VITE_DEV_|X-User-ID|X-Project-ID|X-Unit-ID' -Paths @('frontend/src', 'frontend/Dockerfile', 'compose.yaml')
Assert-NoRgMatch -Pattern 'VITE_DEV_|mock-token|任意密码|admin/123456|/api/auth/refresh' -Paths @('README.md', 'backend/README.md', 'frontend/README.md', 'docs/智能体平台详细功能设计与现状改造清单.md')
Assert-NoRgMatch -Pattern 'mock-token|admin/123456|任意密码|VITE_DEV_|X-User-ID|X-Project-ID|X-Unit-ID' -Paths @('frontend/dist')
Assert-NoRgMatch -Pattern 'TBD|TODO|FIXME|change-me|sk-[A-Za-z0-9]{16,}' -Paths @(
    'backend/app/identity',
    'backend/tests/identity',
    'backend/tests/security/test_deployment_security.py',
    'backend/tests/support/mock_oidc_provider.py',
    'backend/tests/integration/test_identity_migrations.py',
    'backend/tests/integration/test_oidc_mock_flow.py',
    'frontend/src/api/auth.ts',
    'frontend/src/stores/auth.ts',
    'frontend/src/router',
    'frontend/src/views/auth',
    'docs/deployment/oidc-development.md',
    'docs/deployment/aliyun-ecs-http.md'
)

git diff --check
if ($LASTEXITCODE -ne 0) { throw "git diff --check failed" }
git status --short --untracked-files=all
```

The `frontend/dist` scan runs only here, after Step 4 built it. Expected: every negative scan exits cleanly through ripgrep code 1; Git reports only intended implementation changes plus the pre-existing untracked `.task5-harness-root.py`, which remains unread, untouched, unstaged, and uncommitted.

- [ ] **Step 6: Request reviews and commit documentation**

Request a specification review against the OIDC design, then a security review focused on Login CSRF, issuer/sub exactness, Cookie attributes, CSRF plus Origin/Fetch Metadata, grant tuple union, transaction boundaries, single-call structured project reconciliation and atomic audit, session revocation, project-context clearing, frontend 403/409 fail-closed refresh without replay, project-only attempts to invoke global Agents, audit grant-to-SQL filtering, untrusted and trusted forwarding-header behavior, disposable Compose cleanup, and database exposure. Fix all Critical and Important findings and rerun Steps 4 and 5.

```powershell
git add -- frontend/Dockerfile backend/Dockerfile backend/app/identity/client_address.py backend/app/identity/middleware.py backend/tests/identity/test_client_address.py frontend/nginx.conf compose.yaml .env.example backend/tests/test_frontend_dev_identity_config.py backend/tests/security/test_deployment_security.py docs/deployment/oidc-development.md README.md backend/README.md frontend/README.md docs/智能体平台详细功能设计与现状改造清单.md docs/deployment/aliyun-ecs-http.md
git diff --cached --name-only
git commit -m "docs: document oidc authorization foundation"
```

Expected before commit: the staged-name output is exactly the 16 Task 12 files above.

## Final Review Gate

Before starting the `business-resource-authorization` plan:

1. Confirm one Alembic head and successful upgrade/downgrade/upgrade on disposable PostgreSQL.
2. Confirm Mock OIDC covers state, browser correlation, nonce, PKCE, issuer, audience, azp, time, JWKS rotation, UserInfo subject, invalid grant, timeout, and replay.
3. Confirm browsers hold only opaque session and transaction Cookies; Storage and URLs contain no OIDC Token.
4. Confirm multi-project users cannot enter project APIs until they explicitly select a valid project.
5. Confirm the `agent.run + own` and `agent.read + unit` regression does not become `agent.run + unit`.
6. Confirm project-only `agent.run` cannot resolve or invoke a global Agent; selected project plus unit-target `agent.run` is required in this transitional phase.
7. Confirm project/own `audit.read` enters only through scoped-grant admission and every audit list/detail/related query applies the derived SQL predicate.
8. Confirm untrusted forwarding Headers are ignored, one trusted-peer XFF is accepted, and ambiguous trusted-peer XFF is rejected.
9. Confirm all currently mounted routers require session and explicit permission.
10. Confirm frontend visuals remain consistent with the current platform and no global style file changed.
11. Record that business resource object scoping, authorization administration pages, emergency access, Keycloak/real Provider, HTTPS deployment, and logout interoperability are still release blockers.
12. Confirm each unsafe Cookie request requires valid CSRF, exact configured Origin, and strict same-origin Fetch Metadata before its handler runs.
13. Confirm session authentication reconciles the current project exactly once, its structured old-ID/reason result drives the atomic `auth.project.cleared` audit event, and every successful authentication commits a due `last_seen_at` touch even when the project is unchanged.
14. Confirm every frontend 403 and exact context 409 clears routes, menus, caches, and in-flight authenticated requests before one shared `/auth/me` refresh, preserves login, and never replays the denied request.
