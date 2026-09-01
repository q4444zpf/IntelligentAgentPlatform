# Disable Staging Development Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Disable development identity on the public HTTP staging deployment without changing local account authentication or persistent data.

**Architecture:** Keep the deployment in `development` environment until HTTPS and OIDC are available, but close both development-identity entry points. The API receives `IAP_ALLOW_DEV_IDENTITY=false`; the Web image is rebuilt with empty `VITE_DEV_*` build arguments so it neither renders the button nor sends development identity headers.

**Tech Stack:** Docker Compose, FastAPI, Vue 3/Vite, nginx, PostgreSQL 16, OpenSSH

## Global Constraints

- Follow `docs/superpowers/specs/2026-08-26-disable-staging-development-identity-design.md`.
- Target host: `<staging-host>`, SSH port `<ssh-port>`, user `<deploy-user>`.
- Active deployment root: `/home/<deploy-user>/intelligent-agent-platform/current`.
- Keep `IAP_ENVIRONMENT=development` while HTTPS and OIDC remain deferred.
- Never print, replace, or commit generated secrets, passwords, private keys, identity IDs, or unrelated environment values.
- Do not remove volumes, reset PostgreSQL, delete releases, or recreate unrelated services.
- This is a configuration-only change. It uses a pre-change behavior/configuration baseline and post-change regression in place of source-code TDD; execution requires the user's explicit approval for this exception.

---

### Task 1: Close Both Development Identity Entry Points

**Files:**
- Modify on server: `/home/<deploy-user>/intelligent-agent-platform/current/.env`
- Create on server: `/home/<deploy-user>/intelligent-agent-platform/backups/<timestamp>-pre-disable-dev-identity.env`

**Interfaces:**
- Consumes: `compose.yaml`, `compose.http-staging.yaml`, existing generated secrets, existing API and Web images.
- Produces: API runtime with development identity disabled and Web assets compiled without development identity configuration.

- [ ] **Step 1: Capture the pre-change baseline without exposing values**

Run over SSH from the active deployment root:

```bash
docker compose -f compose.yaml -f compose.http-staging.yaml config --format json \
  | python3 -c 'import json,sys; c=json.load(sys.stdin); a=c["services"]["api"]["environment"]; w=c["services"]["web"]["build"]["args"]; print({"api_dev_identity_enabled": a.get("IAP_ALLOW_DEV_IDENTITY")=="true", "web_dev_identity_configured": all(bool(w.get(k)) for k in ("VITE_DEV_UNIT_ID","VITE_DEV_USER_ID","VITE_DEV_PROJECT_ID"))})'
```

Expected pre-change output:

```text
{'api_dev_identity_enabled': True, 'web_dev_identity_configured': True}
```

- [ ] **Step 2: Back up the active environment file**

Run over SSH:

```bash
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup=/home/<deploy-user>/intelligent-agent-platform/backups/${stamp}-pre-disable-dev-identity.env
cp -- .env "$backup"
chmod 600 "$backup"
printf '%s\n' "$backup"
```

Expected: one explicit backup path under `/home/<deploy-user>/intelligent-agent-platform/backups/` and mode `600`.

- [ ] **Step 3: Replace only the six existing development identity settings**

Run over SSH from the active deployment root:

```bash
sed -i -E \
  -e 's/^IAP_ALLOW_DEV_IDENTITY=.*/IAP_ALLOW_DEV_IDENTITY=false/' \
  -e 's/^IAP_DEV_IDENTITY_TRUSTED_CIDRS=.*/IAP_DEV_IDENTITY_TRUSTED_CIDRS=/' \
  -e 's/^VITE_DEV_UNIT_ID=.*/VITE_DEV_UNIT_ID=/' \
  -e 's/^VITE_DEV_PROJECT_ID=.*/VITE_DEV_PROJECT_ID=/' \
  -e 's/^VITE_DEV_USER_ID=.*/VITE_DEV_USER_ID=/' \
  -e 's/^VITE_DEV_USER_ROLES=.*/VITE_DEV_USER_ROLES=/' \
  .env
```

Do not add `VITE_DEV_USER_ROLE`; it is absent from the active environment and the Compose default is already empty.

- [ ] **Step 4: Verify the merged configuration before recreating services**

Run the Step 1 command again.

Expected post-change output:

```text
{'api_dev_identity_enabled': False, 'web_dev_identity_configured': False}
```

Also verify the six expected keys still occur exactly once without printing values:

```bash
for key in IAP_ALLOW_DEV_IDENTITY IAP_DEV_IDENTITY_TRUSTED_CIDRS VITE_DEV_UNIT_ID VITE_DEV_PROJECT_ID VITE_DEV_USER_ID VITE_DEV_USER_ROLES; do
  test "$(grep -c "^${key}=" .env)" -eq 1 || exit 1
done
```

- [ ] **Step 5: Rebuild Web and recreate only API and Web**

Run over SSH:

```bash
docker compose -f compose.yaml -f compose.http-staging.yaml --profile sandbox build web
docker compose -f compose.yaml -f compose.http-staging.yaml --profile sandbox up -d --no-deps --force-recreate api web
```

Expected: both services are recreated successfully; persistent volumes remain attached.

- [ ] **Step 6: Verify service health**

Run over SSH:

```bash
docker compose -f compose.yaml -f compose.http-staging.yaml --profile sandbox ps
docker compose -f compose.yaml -f compose.http-staging.yaml --profile sandbox exec -T api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/health').status)"
```

Expected: API, Web, PostgreSQL, MinIO, Workflow Runner, and Sandbox Launcher are running; the health request prints `200`.

### Task 2: Verify Rejection, Local Login, Session Persistence, and Logs

**Files:**
- No files modified.

**Interfaces:**
- Consumes: public system URL, previously supplied local account, deployed API/Web services.
- Produces: fresh acceptance evidence for the security change.

- [ ] **Step 1: Verify the public login page hides development login**

Open `http://<staging-host>:<http-port>/login` in the in-app browser and capture a fresh DOM snapshot.

Expected: `本地账号登录` and `统一认证登录` are visible; `开发身份登录（仅开发环境）` is absent.

- [ ] **Step 2: Verify the backend rejects direct development login**

Send a request with non-sensitive placeholder headers and do not retain Cookies:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -X POST \
  -H 'X-User-ID: disabled-check' \
  -H 'X-Unit-ID: disabled-check' \
  -H 'X-Project-ID: disabled-check' \
  http://<staging-host>:<http-port>/api/auth/dev/login
```

Expected: HTTP `401`.

- [ ] **Step 3: Verify real local account login**

Immediately before entering the previously supplied email and password, obtain the user's confirmation to transmit those credentials to the deployment. Submit `本地账号登录`, record the elapsed time, and verify navigation to `/dashboard`.

Expected: the button stops loading and Dashboard becomes visible.

- [ ] **Step 4: Verify session persistence and browser errors**

Reload Dashboard, wait for the DOM to settle, and inspect browser console errors.

Expected: the URL remains `/dashboard`, `平台运行总览` remains visible, and the console error list is empty.

- [ ] **Step 5: Verify recent server logs**

Run over SSH:

```bash
docker compose -f compose.yaml -f compose.http-staging.yaml --profile sandbox logs --since=15m --tail=400 --no-color api web
```

Expected evidence includes the rejected development login, successful local login, successful `/api/auth/me`, overview, and services requests. There must be no related `ERROR`, `Traceback`, or HTTP 500.

- [ ] **Step 6: Record the result and rollback path**

Report the backup path, service health, direct API rejection status, local login result, reload result, browser console error count, and server log findings. If any acceptance criterion fails, restore the backup named in Task 1 Step 2, rebuild Web, recreate API/Web, and report the failed criterion without claiming completion.
