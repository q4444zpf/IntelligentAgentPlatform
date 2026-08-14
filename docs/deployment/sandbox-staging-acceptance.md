# Sandbox Staging Acceptance

This procedure is for a non-production staging environment. Never place Runner signing keys, Launcher tokens, model credentials or customer data in source control, Compose files or shell history.

## 1. Inject staging credentials

Inject both `IAP_RUNNER_TOKEN_SIGNING_KEY` and `IAP_RUNNER_LAUNCHER_TOKEN` through the deployment secret manager. The signing key must contain at least 32 bytes. Keep `IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED=false` until every check below passes.

Set these non-secret identities in deployment configuration:

```text
IAP_RUNNER_GATEWAY_URL=http://api:8000/internal/runner
IAP_RUNNER_GATEWAY_NETWORK=intelligent-agent-platform_runner-gateway
```

Do not store real secret values in `.env`, CI logs or Git.

## 2. Start the controlled launcher

```powershell
docker compose --profile sandbox up -d --build api workflow-runner sandbox-launcher
docker compose --profile sandbox ps sandbox-launcher
```

The API and Launcher must refuse sandbox startup when their required secret is empty. The default Compose profile does not start the Launcher.

Verify authenticated health without printing the token:

```powershell
$headers = @{ Authorization = "Bearer $env:IAP_RUNNER_LAUNCHER_TOKEN" }
Invoke-RestMethod -Uri http://127.0.0.1:8091/health -Headers $headers
```

## 3. Verify boundaries

- Request `/health` without a Bearer token: expect `401`.
- Request `/health` with the staging token: expect `200` and `status=healthy`.
- Confirm the `runner-gateway` network is internal and only the API, Workflow Runner, Launcher and active Run containers are attached.
- Confirm Workflow Runner and Run containers have no `/var/run/docker.sock`; only the isolated Launcher mounts it.
- Create a test Run and confirm its container is non-root, non-privileged, read-only, resource-limited, has `CapDrop=ALL`, and joins only `intelligent-agent-platform_runner-gateway`.
- Confirm the Run container environment contains only `IAP_RUN_EXECUTION_REQUEST` and `IAP_RUNNER_GATEWAY_URL`. Inspect variable names only; do not print values.
- Exercise success, timeout, cancellation, OOM, invalid workspace, and duplicate Run cases.
- Confirm Run artifacts are downloadable during the Run and their object-store entries are removed when explicitly deleted.

Useful inspection commands:

```powershell
docker network inspect intelligent-agent-platform_runner-gateway
docker inspect iap-run-<run-id> --format '{{.Config.User}} {{.HostConfig.ReadonlyRootfs}} {{.HostConfig.Privileged}} {{json .HostConfig.CapDrop}} {{.HostConfig.NetworkMode}}'
docker inspect iap-run-<run-id> --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'
docker inspect iap-run-<run-id> --format '{{range .Config.Env}}{{println (index (split . "=") 0)}}{{end}}'
```

## 4. Roll back

1. Set `IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED=false` and restart the API/Workflow Runner.
2. For every active sandbox Run, call the authenticated `POST /api/agent-runs/{run_id}/cancel` endpoint. This terminates the container and revokes its Run token.
3. Confirm no active Run remains, then remove any residual Run containers and the sandbox profile services:

```powershell
docker ps --filter "name=iap-run-" --format "{{.Names}}"
docker ps --filter "name=iap-run-" -q | ForEach-Object { docker rm -f $_ }
docker compose --profile sandbox rm -sf workflow-runner sandbox-launcher
```

4. Confirm the API remains healthy in local Harness mode. Remove the internal network only after no container is attached:

```powershell
docker network rm intelligent-agent-platform_runner-gateway
```

Keep `IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED=false` unless every acceptance check passes and the staging owner approves the change.
