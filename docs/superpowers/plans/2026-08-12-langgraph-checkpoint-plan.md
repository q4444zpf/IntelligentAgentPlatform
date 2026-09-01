# LangGraph Checkpoint Plan

## Completed

- Add `runtime_checkpoints` metadata table and migration.
- Add JSON-only, per-Run `CheckpointStore` with idempotent keys.
- Restore latest state before graph invocation and save after invocation.
- Keep checkpoint state separate from RunEvent and Artifact binary content.

## Next Steps

1. Implement Workflow Runner service boundary and health contract.
2. Run LangGraph/Deep Agents only inside the sandbox runner.
3. Add end-to-end recovery test using PostgreSQL and a published Agent snapshot.

## Approval Recovery Completed

- Save `waiting_approval` runtime state on approval interruption.
- Inject `CheckpointStore` into the default Dispatcher/Harness execution path.
- Persist terminal `completed` state after approved tool execution resumes.
