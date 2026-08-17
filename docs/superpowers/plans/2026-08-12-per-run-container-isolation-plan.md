# Per-Run Container Isolation Plan

## Completed

- Add fixed `ContainerPolicy` for trusted runner images.
- Enforce non-privileged, read-only root, dropped capabilities and no network.
- Set memory, CPU and PID limits in the generated container configuration.
- Validate Run IDs and absolute per-Run workspace paths.
- Add `ContainerLauncher` with injected Docker client and forced cleanup.
- Fail closed when Docker client is unavailable.

## Remaining Before Enabling `sandbox=true`

1. Provide a dedicated runner image containing LangGraph/DeepAgents runtime.
2. Use a controlled Docker/CRI launcher outside the API container.
3. Mount immutable execution package and scoped workspace only.
4. Add network, escape, resource exhaustion and cancellation tests.
5. Configure MinIO output upload and cleanup verification.
