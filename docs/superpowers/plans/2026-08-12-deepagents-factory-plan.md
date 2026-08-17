# Deep Agents Factory Plan

## Completed

- Define immutable published Agent and Tool snapshots.
- Filter unpublished or disabled tools before Agent creation.
- Combine system and context prompts deterministically.
- Provide an injectable creator for tests and a production `create_deep_agent` creator.

## Next

1. Adapt platform ToolGateway tools to the Deep Agents tool protocol.
2. Add a LangGraph node that invokes the factory-created agent.
3. Add checkpoint persistence and interruption recovery in Workflow Runner.
4. Keep API/Worker from executing untrusted Agent code outside the sandbox.
