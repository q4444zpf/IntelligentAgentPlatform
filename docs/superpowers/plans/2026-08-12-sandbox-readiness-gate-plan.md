# Sandbox Readiness Gate Plan

## Completed

- Add explicit `SandboxReadiness` safety proof object.
- Require six controls before `sandbox=true`:
  - trusted runner image
  - non-root execution
  - read-only root filesystem
  - network disabled
  - resource limits
  - guaranteed cleanup
- Include missing controls in `/health` diagnostics.
- Keep `/runs` fail-closed with HTTP 503 until all controls pass.
- Add Compose environment variables, all defaulting to `false`.
- Rebuild the dedicated Runner image and verify the gate in-container.

## Remaining Before Production Enablement

1. Connect readiness values to measured launcher/container state instead of operator flags.
2. Add escape, network, resource exhaustion and cancellation acceptance tests.
3. Enable the gate only in a controlled staging environment first.
