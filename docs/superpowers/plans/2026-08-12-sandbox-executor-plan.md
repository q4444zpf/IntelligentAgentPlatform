# Sandbox Executor Plan

## Completed

- Add disabled-by-default `SandboxExecutor`.
- Create a per-Run temporary workspace and always clean it up.
- Enforce operation timeout and report safe timeout errors.
- Restrict execution to server-registered operation names.
- Reject arbitrary Shell strings and user-provided callables at the Runner API boundary.

## Remaining Security Work

1. Launch each Run in a separate container or microVM.
2. Enforce CPU, memory, PID, filesystem, network and wall-clock limits.
3. Mount only immutable execution package plus virtual workspace.
4. Stream artifacts through MinIO and prevent host path exposure.
5. Add cancellation, kill, cleanup and escape-resistance tests.
