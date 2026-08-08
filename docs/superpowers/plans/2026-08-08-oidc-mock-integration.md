# OIDC Mock Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete a repeatable development/test OIDC Authorization Code + PKCE flow while retaining the local development identity adapter.

**Architecture:** Keep tokens server-side. The FastAPI BFF obtains provider metadata and exchanges the authorization code, validates ID Token claims, binds the external subject to the existing PostgreSQL identity model, and issues the opaque platform session Cookie. A protocol-level Mock Provider is test-only and is never enabled in production.

**Tech Stack:** FastAPI, HTTPX, Authlib JOSE primitives already in the backend, SQLAlchemy/PostgreSQL, pytest, Vue 3, Vitest.

## Global Constraints

- Mock Provider is allowed only in `development` or `test` environments.
- Browser storage and URLs must never contain OIDC Access, Refresh, or ID Tokens.
- Validate issuer, audience, azp, nonce, state, PKCE, time claims, and one-time authorization-code use.
- Preserve the current local development login path and existing visual shell.

### Task 1: OIDC Client Contract

**Files:**
- Create: `backend/app/identity/oidc.py`
- Create: `backend/tests/identity/test_oidc_client.py`

- [ ] Write failing tests for discovery issuer matching, authorization URL PKCE parameters, token exchange failures, and strict ID Token claim validation.
- [ ] Run `python -m pytest tests/identity/test_oidc_client.py -q` and confirm failure because the client module is absent.
- [ ] Implement the minimal discovery, authorization URL, code exchange, and claim validation functions.
- [ ] Re-run the focused tests and confirm they pass.

### Task 2: Protocol-Level Mock Provider

**Files:**
- Create: `backend/tests/support/mock_oidc_provider.py`
- Create: `backend/tests/integration/test_oidc_mock_flow.py`

- [ ] Add a test provider fixture exposing discovery, authorize, token, and JWKS responses with controllable claims and replay behavior.
- [ ] Add a failing end-to-end test for successful authorization-code + PKCE login and platform session creation.
- [ ] Add failures for state/nonce mismatch, missing browser correlation, expired code, invalid audience, and code replay.
- [ ] Run the integration tests and confirm the expected failures before wiring the application.

### Task 3: BFF Callback Integration

**Files:**
- Modify: `backend/app/identity/auth_router.py`
- Modify: `backend/app/identity/session_lifecycle.py`
- Test: `backend/tests/integration/test_oidc_mock_flow.py`

- [ ] Connect `/api/auth/login` to the OIDC client and persist only hashed transaction state and encrypted PKCE verifier.
- [ ] Connect `/api/auth/callback` to code exchange, external identity binding, permission sync, and opaque session issuance.
- [ ] Add logout and session renewal assertions, including Cookie clearing and token non-disclosure.
- [ ] Run backend identity and integration tests.

### Task 4: Frontend Login Contract

**Files:**
- Modify: `frontend/src/api/auth.ts`
- Modify: `frontend/src/router/routes.ts`
- Modify: `frontend/src/views/auth/LoginView.vue`
- Create/Modify: `frontend/src/api/auth.test.ts`

- [ ] Add failing tests for local-login visibility, OIDC redirect action, callback bootstrap, logout, and session-expiry handling.
- [ ] Implement the smallest UI/API changes while preserving the existing layout and local development identity option.
- [ ] Run focused Vitest tests and the production build.

### Task 5: Verification and Documentation

**Files:**
- Modify: `docs/deployment/oidc-development.md`
- Modify: `README.md` only if the documented local flow is stale.

- [ ] Run backend identity/integration tests, frontend focused tests, full build, `git diff --check`, and secret scanning.
- [ ] Verify `GET /api/health` and Docker service health.
- [ ] Record the local Mock OIDC test flow and production configuration prerequisites.
- [ ] Commit with `feat: add mock oidc integration flow`.
