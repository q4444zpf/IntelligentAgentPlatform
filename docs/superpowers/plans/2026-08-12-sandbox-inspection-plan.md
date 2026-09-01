# Sandbox Inspection Plan

## Completed

- Add `SandboxInspector` for Docker inspect-style runtime evidence.
- Verify trusted image, non-root user, read-only root, network none, resource limits.
- Verify privileged mode is disabled and all capabilities are dropped.
- Verify cleanup guarantee label.
- Feed inspected readiness into Runner health and submission gating.
- Keep environment variables as opt-in flags only; they cannot override missing evidence.

## Remaining

1. Connect an external, read-only Docker/CRI inspect channel.
2. Inspect the actual per-Run container after launch, not only the Runner container.
3. Add escape and cancellation acceptance tests against a staging launcher.
4. Enable `sandbox=true` only after measured inspect evidence and artifact cleanup pass.
