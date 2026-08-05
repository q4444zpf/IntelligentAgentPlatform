# Identity Administration and OIDC Completion Plan

**Goal:** Complete unit-scoped user, project, role, permission administration and finish the production OIDC Web session flow while retaining the loopback-only development identity adapter.

**Authoritative design:** `docs/superpowers/specs/2026-08-04-oidc-local-authorization-design.md`

## Global Constraints

- PostgreSQL is the sole identity, session, role, permission, and project authorization store.
- Every management query and mutation is scoped by the authenticated unit; project resources must additionally enforce project membership and scope.
- Ordinary users never receive local passwords. External identities bind by exact `issuer + subject` only.
- Browser OIDC tokens remain server-side. The browser receives only an opaque HttpOnly session Cookie and memory-only CSRF token.
- Keep development identity headers only in explicit development/test mode on loopback.
- Every mutation records an audit event and increments affected users' authorization versions so existing sessions refresh or fail closed.
- Built-in roles and the protected default administrator cannot be deleted; use status changes where permitted.
- Add tests for every behavior change. Never read, edit, stage, or commit `.task5-harness-root.py`.

## Tasks

### Task 1: Complete read-only identity administration slice

- [ ] Finish unit-scoped list APIs for users, units, projects, roles, and permissions.
- [ ] Return role bindings and project memberships needed by the administration UI without cross-unit leakage.
- [ ] Connect `/system/users` and `/system/roles` to real APIs with loading, error, empty, and refresh states.
- [ ] Add backend and frontend tests, then commit the task.

### Task 2: Add user and external identity mutations

- [ ] Add create, update, activate/deactivate, exact OIDC identity binding, and role assignment APIs.
- [ ] Protect the default administrator from deletion/deactivation that would remove the final active unit administrator.
- [ ] Add audit events, authorization-version updates, tests, and user-page forms.

### Task 3: Add unit and project administration

- [ ] Add unit update and project create/update/activate/deactivate APIs.
- [ ] Enforce single-unit deployment constraints and cross-unit isolation.
- [ ] Connect the unit/project administration page and add tests.

### Task 4: Add role and permission administration

- [ ] Add custom role create/update/activate/deactivate and permission grant APIs.
- [ ] Protect built-in roles and validate unit/project data scopes.
- [ ] Connect `/system/roles` with real role, grant, and assignment operations and add tests.

### Task 5: Complete OIDC login and server-side session flow

- [ ] Implement discovery, PKCE login transaction, callback validation, exact identity binding, and opaque Cookie sessions.
- [ ] Implement `/api/auth/me`, local logout, idle renewal, absolute expiry, CSRF protection, and authorization-version synchronization.
- [ ] Preserve the loopback-only development identity adapter and add Mock OIDC protocol tests.

### Task 6: Verify isolation and complete review

- [ ] Run PostgreSQL integration tests for one unit, multiple projects, multiple roles, and denied cross-project/cross-unit access.
- [ ] Run frontend tests and production build.
- [ ] Perform task reviews and final whole-branch review, then prepare the branch for integration.
