# ToolGateway and LangGraph Node Plan

## Completed

- Adapt Deep Agents tool calls to the existing ToolGateway contract.
- Preserve server-owned execution context and authorized tool IDs.
- Convert gateway failures to safe runtime errors.
- Add a LangGraph-compatible DeepAgent node that updates messages/status.

## Next Steps

1. Add checkpoint persistence for graph state and approval interruptions.
2. Emit node start/completion events into RunEvent without raw prompts or binary data.
3. Run the graph only inside Workflow Runner Sandbox Executor.
4. Add a production smoke test with a real LangGraph graph and a published builtin tool.
