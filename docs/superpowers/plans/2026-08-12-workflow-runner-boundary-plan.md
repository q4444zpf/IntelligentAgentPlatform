# Workflow Runner Boundary Plan

## Completed

- Add a health-gated Workflow Runner client contract.
- Submit only Run, Agent version and Checkpoint references.
- Add an independent FastAPI Runner service with `/health` and `/runs`.
- Fail closed with HTTP 503 while Sandbox Executor is disabled.
- Add a read-only, no-capabilities, no-new-privileges Compose service.

## Security Status

The service boundary is implemented, but a healthy container is not yet a per-Run sandbox. `sandbox=false` remains the required default and execution is rejected.

## Next Steps

1. Implement a per-Run container launcher with CPU, memory, PID, network and timeout limits.
2. Mount only an immutable execution package and a virtual workspace.
3. Route output through Artifact Service instead of host filesystem paths.
4. Add termination, cancellation and cleanup tests.
5. Enable `sandbox=true` only after security and recovery acceptance tests pass.
