# Disable Staging Development Identity Design

## Goal

Disable the development identity login path on the public HTTP staging deployment while preserving local account login and the existing deployed services.

## Context

The staging server currently runs with `IAP_ENVIRONMENT=development` because the backend's production startup validation requires HTTPS and a complete OIDC configuration. HTTPS and OIDC are explicitly deferred, so this change must not set `IAP_ENVIRONMENT=production`.

Development identity is controlled in two places:

- The backend permits `POST /api/auth/dev/login` only when `IAP_ALLOW_DEV_IDENTITY=true` outside production.
- The frontend includes the development login button only when all required `VITE_DEV_*` identity build arguments are populated.

## Design

Use deployment configuration as the single change surface:

1. Set `IAP_ALLOW_DEV_IDENTITY=false` in the active staging environment file.
2. Clear `IAP_DEV_IDENTITY_TRUSTED_CIDRS` because no development identity peer remains trusted.
3. Clear `VITE_DEV_UNIT_ID`, `VITE_DEV_PROJECT_ID`, `VITE_DEV_USER_ID`, `VITE_DEV_USER_ROLES`, and `VITE_DEV_USER_ROLE`.
4. Recreate the API container so it receives the disabled backend setting.
5. Rebuild and recreate the Web container so the compiled login page omits the development identity button and development identity headers.

No application source code, database schema, user record, password, or active authorization role is changed.

## Security Behavior

After deployment:

- The login page exposes local account login and unified authentication login only.
- Direct requests to `POST /api/auth/dev/login` return an authentication rejection and cannot create a session.
- Local password authentication continues to issue the existing HttpOnly session Cookie.
- The deployment remains an HTTP staging environment. HTTPS, secure Cookies, production mode, OIDC, and managed secrets remain separate follow-up work.

## Deployment Safety

Before editing, create a timestamped backup of the active environment file on the server. Apply only the named variables and preserve all generated secrets and unrelated configuration. Render the merged Compose configuration before recreation to confirm that the backend receives `false` and the frontend build arguments are empty.

Recreate only the API and Web services plus dependencies required by Compose. Do not remove volumes, reset PostgreSQL, delete releases, or rotate secrets in this change.

## Verification

Acceptance requires fresh evidence for every item:

1. The merged Compose configuration resolves `IAP_ALLOW_DEV_IDENTITY` to `false` and all development identity Web build arguments to empty strings.
2. API, Web, PostgreSQL, MinIO, Workflow Runner, and Sandbox Launcher are healthy after recreation.
3. The public login page does not contain the development identity login button.
4. A direct development-login request is rejected without creating a session.
5. The previously validated local account can log in and reach Dashboard.
6. Reloading Dashboard preserves the local account session.
7. Browser console logs contain no errors.
8. Recent API and Web logs contain no traceback, server error, or HTTP 500 related to the deployment and login checks.

## Rollback

Restore the timestamped environment-file backup, rebuild Web, and recreate API and Web. Rollback does not require database changes.
