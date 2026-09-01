# Docker Inspect Channel Plan

## Completed

- Add read-only `DockerInspectTransport`.
- Return no data on missing container or inspect errors.
- Feed inspect results into `SandboxInspector` and Runner health gate.
- Mark `container_inspection` as missing when the inspected Run container is unavailable.
- Keep Docker Socket outside the Runner container.

## Progress

- [x] Add a run-scoped launcher API with Bearer authentication.
- [x] Require `X-Run-Id` to match the URL run scope.
- [x] Add create, inspect, terminate, and cleanup lifecycle operations.
- [x] Keep empty authentication configuration unavailable (`503`).
- [x] Add unit/API tests for authentication, scope isolation, and cleanup.
- [x] Add an independently packaged launcher service behind the `sandbox` Compose profile.
- [x] Keep the Docker Socket mount restricted to the launcher profile; Runner has no socket mount.
- [x] Verify the complete runtime/artifact regression suite and live service health.
- [x] Inspect container attributes immediately after creation and reject unsafe containers.
- [x] Force cleanup when post-create readiness inspection fails.
- [x] Map readiness failures to HTTP 503 at the launcher boundary.
- [x] Reject duplicate containers for the same Run.
- [x] Force-stop containers during cancellation/termination.
- [x] Guarantee cleanup when execution wait times out or raises.
- [x] Restrict workspaces to the canonical `/workspace/{run_id}` path.
- [x] Reject path traversal and cross-Run workspace paths.
- [x] Make cleanup state scoped and non-reusable after deletion.
- [x] Run staging Launcher with a temporary token and verify unauthorized/authorized health behavior.
- [x] Verify the Runner container has no Docker Socket while Launcher alone can access the staging Socket.
- [x] Inspect a real per-Run Runner container and verify all readiness controls.
- [x] Verify read-only root behavior and network isolation inside a real staging container.
- [x] Remove all temporary staging containers after verification.
- [x] Verify real-container memory exhaustion is reported as OOM and cleaned up.
- [x] Verify forced termination exits the container and cleanup completes.
- [x] Confirm no staging test containers remain after resource/cancellation checks.
- [x] Verify escape boundaries: no Docker Socket, non-root identity, non-privileged, and dropped capabilities.
- [x] Verify Run Artifact deletion removes the MinIO/S3 object as well as the database record visibility.
- [x] Add the Runner-to-Launcher client with Bearer and Run-scoped headers.
- [x] Require container creation and inspect before Runner accepts a sandbox Run.
- [x] Clean up the container when inspect fails or reports a non-running status.
- [x] Keep the client disabled unless both Launcher URL and token are configured.

## Remaining

1. Keep `sandbox=false` in the default deployment.
2. For a controlled staging enablement, inject a managed launcher token and run the documented acceptance suite. (Verified with a temporary token; production secret management remains deployment-owned.)
