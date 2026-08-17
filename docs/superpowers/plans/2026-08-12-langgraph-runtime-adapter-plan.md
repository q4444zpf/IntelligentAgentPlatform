# LangGraph / Deep Agents Runtime Adapter Plan

## Completed In This Step

- Add a framework-neutral `LangGraphRuntimeAdapter` boundary.
- Normalize run state to `run_id`, messages, status and extension values.
- Pass `thread_id=run_id` and run metadata through LangGraph config.
- Require a non-empty assistant result before a run can be considered complete.
- Declare `langchain-core`, `langgraph` and `deepagents` as worker-runtime dependencies.

## Next Steps

1. Add a PostgreSQL-backed LangGraph checkpointer in the isolated Workflow Runner.
2. Build a Deep Agents factory from the published Agent/Skill/Tool snapshot.
3. Wrap platform ToolGateway as the only callable tool surface.
4. Stream graph node events into RunEvent and persist intermediate files as Artifacts.
5. Enable the adapter only when Sandbox Executor health and checkpoint recovery tests pass.
