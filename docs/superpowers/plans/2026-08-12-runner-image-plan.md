# Dedicated Workflow Runner Image Plan

## Completed

- Add `backend/Dockerfile.runner` separate from the API image.
- Start only `workflow_runner_api` on port 8090.
- Run as `nobody` without database migrations or legacy imports.
- Build and start the image through Compose.
- Verify container UID, health endpoint and default `sandbox=false`.

## Remaining

1. Add the real LangGraph/DeepAgents runner entrypoint to this image.
2. Connect an external controlled Docker/CRI launcher.
3. Mount immutable execution packages and scoped workspaces per Run.
4. Enable sandbox only after escape, resource, network and cancellation tests.
