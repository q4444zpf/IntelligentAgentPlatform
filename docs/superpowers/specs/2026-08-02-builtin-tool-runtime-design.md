# Built-in Tool Runtime Design

## 1. Goal And Scope

This phase adds the first production tool-calling loop to the existing single-agent runtime. It establishes a reusable Tool Registry and Tool Gateway, makes tool authorization an explicit part of agent configuration, and delivers two low-risk built-in tools:

- `system.get_current_time`
- `system.get_runtime_context`

The phase does not execute remote MCP tools, stdio MCP, Shell commands, files, user code, knowledge retrieval, GIS operations, LangGraph, or Deep Agents. Existing MCP management remains a connection and discovery control plane. A synchronized MCP tool must be reviewed and published into the Tool Registry in a later phase before an agent can execute it.

The first slice succeeds when an authorized default agent can answer questions about the current date by calling a registered tool, the complete invocation is persisted and visible through Run events, and an agent cannot call an unbound or disabled tool.

## 2. Existing System Boundary

The current platform already provides:

- PostgreSQL-backed agent and MCP configuration;
- agent-bound prompts, model selection, Skill names, and approval policy;
- a persisted Conversation, Message, AgentRun, and RunEvent model;
- a `PlatformAgentHarness` that performs one OpenAI-compatible model request;
- MCP HTTP/SSE tool discovery and per-client whitelist management;
- a Web chat that polls Run events and reloads completed messages.

The current runtime does not expose tool schemas to the model, parse `tool_calls`, authorize tools, execute tools, or persist tool invocations. Skill binding remains behavioral context and must not be treated as tool authorization.

## 3. Chosen Architecture

The platform will extend the existing Harness with a bounded, framework-neutral Tool Loop. It will not introduce LangChain, Deep Agents, or LangGraph in this phase.

```text
AgentRun
  -> resolve concrete agent and bound tools
  -> Tool Registry returns enabled published definitions
  -> model request receives only authorized tool schemas
  -> model returns content or tool_calls
  -> Tool Gateway validates authorization and arguments
  -> built-in executor runs trusted deterministic code
  -> invocation and Run events are persisted
  -> tool result is appended to model messages
  -> model produces the final assistant response
```

The boundaries are:

- **Tool Registry** owns tool identity, version, schemas, source, risk, publication, and enabled state.
- **Tool Gateway** owns authorization, argument validation, execution dispatch, result normalization, limits, audit summaries, and safe errors.
- **Built-in executors** are fixed Python callables registered by exact tool ID. They cannot dynamically import code or invoke subprocesses.
- **Model Gateway** translates between platform message/tool value objects and the OpenAI-compatible request and response format.
- **Harness** owns the bounded conversation with the model and persists Run state transitions and events.

These interfaces remain reusable when MCP, knowledge, Artifact, sandbox, Deep Agents, and LangGraph are added later.

## 4. Tool Registry Data Model

Add a `registered_tools` table with the following logical fields:

| Field | Meaning |
| --- | --- |
| `tool_id` | Stable resource ID such as `system.get_current_time` |
| `version` | Immutable semantic version for the executable contract |
| `name` | User-facing Chinese display name |
| `description` | Model-facing and administrator-facing description |
| `source` | `builtin` in this phase; later `mcp`, `knowledge`, `artifact`, or `sandbox` |
| `risk_level` | `low`, `medium`, `high`, or `critical` |
| `input_schema` | JSON Schema for model arguments |
| `output_schema` | JSON Schema for normalized results |
| `requires_approval` | Whether Tool Gateway must stop for approval |
| `published` | Whether agents may bind the definition |
| `enabled` | Platform-wide execution switch |
| `created_at`, `updated_at` | Audit timestamps |

The primary identity for this phase is `tool_id`; each built-in tool starts at version `1.0.0`. A later versioning phase may split immutable releases from the mutable registry pointer without changing agent-facing tool IDs. Every invocation stores the resolved version so historical Runs remain explainable.

Add `tool_ids: list[str]` to agent configuration. It is independent from `skill_names`. Agent create, update, copy, initialization, and response schemas preserve and validate this list. A bound tool must exist, be published, and be enabled when an agent configuration is saved. Runtime authorization revalidates the binding so disabling a tool takes effect without trusting stale agent configuration.

The built-in default agent is initialized with both built-in tool IDs. Existing installations are repaired idempotently: missing built-in bindings are added without removing administrator-added bindings.

## 5. Invocation And Audit Model

Add a `tool_invocations` table:

| Field | Meaning |
| --- | --- |
| `id` | UUID invocation ID |
| `run_id` | Owning AgentRun |
| `tool_call_id` | Model-provided call ID, unique within the Run |
| `tool_id`, `tool_version` | Resolved executable contract |
| `status` | `started`, `completed`, or `failed` |
| `arguments_summary` | Length-limited, recursively redacted JSON |
| `result_summary` | Length-limited, recursively redacted JSON |
| `error_code` | Stable safe failure code or null |
| `duration_ms` | Execution duration |
| `created_at`, `completed_at` | Audit timestamps |

Raw secrets, authorization headers, internal stack traces, full large results, and Python representations are never stored. The summary redactor masks keys containing `authorization`, `api_key`, `apikey`, `token`, `secret`, `password`, or `credential`, limits nesting and collection sizes, and caps serialized length.

Run events reference the invocation ID and contain only display-safe data:

```json
{
  "event_type": "tool.started",
  "payload": {
    "invocation_id": "uuid",
    "tool_id": "system.get_current_time",
    "display_name": "获取当前时间"
  }
}
```

`tool.completed` adds `duration_ms`; `tool.failed` adds a stable `code` and safe `message`. Full normalized tool output is returned to the model but not copied into RunEvent payloads.

## 6. Built-in Tool Contracts

### 6.1 `system.get_current_time`

Input:

```json
{
  "type": "object",
  "properties": {
    "timezone": {
      "type": "string",
      "description": "IANA timezone name; defaults to Asia/Shanghai"
    }
  },
  "additionalProperties": false
}
```

Output:

```json
{
  "iso_datetime": "2026-08-02T12:30:00+08:00",
  "date": "2026-08-02",
  "time": "12:30:00",
  "weekday": "Sunday",
  "weekday_zh": "星期日",
  "timezone": "Asia/Shanghai"
}
```

The executor uses `datetime.now(ZoneInfo(timezone))`. Invalid or unavailable IANA names fail with `tool_invalid_arguments`. It does not use Shell commands, files, network access, locale-dependent formatting, or client-provided clock values.

### 6.2 `system.get_runtime_context`

Input is an empty object with `additionalProperties: false`.

Output contains:

```json
{
  "current_time": "2026-08-02T12:30:00+08:00",
  "timezone": "Asia/Shanghai",
  "user_id": "dev-user",
  "project_id": "dev-project",
  "conversation_id": "uuid",
  "run_id": "uuid"
}
```

All identity and resource fields come from the persisted Conversation and AgentRun selected by the server. The model cannot provide or override them. The tool excludes DOM, UI component state, browser storage, access tokens, API keys, server paths, environment variables, and host information. Page and GIS context require the separately versioned `ui.context` protocol in a later phase.

## 7. Model Gateway Contract

Replace the content-only model result with structured platform value objects:

- `ToolDefinition`: tool ID, description, and input schema supplied to the provider.
- `ToolCall`: provider call ID, tool ID, and parsed JSON arguments.
- `ModelResult`: optional text content, zero or more ToolCalls, and token usage.

`ModelGateway.generate` accepts messages, optional model selection, and authorized tool definitions. The OpenAI-compatible adapter:

- sends tools as `type=function` definitions;
- uses `tool_choice=auto` only when at least one tool is authorized;
- accepts assistant responses with content, tool calls, or both;
- validates tool call IDs, names, and JSON arguments;
- preserves assistant tool-call messages and `role=tool` result messages for the next iteration;
- never exposes provider response bodies or credentials in domain errors.

A response with neither content nor valid tool calls is a model upstream failure.

## 8. Bounded Tool Loop

The Harness performs at most four model iterations. Across the Run, at most eight tool calls may execute. A single assistant response may request multiple calls, which execute sequentially in the first phase to keep persistence and failure semantics deterministic.

For each call:

1. Confirm the requested tool ID is in the Run agent's resolved authorization set.
2. Confirm the registry definition remains published and enabled.
3. Validate arguments against the registered JSON Schema.
4. Create the invocation and `tool.started` event in one transaction.
5. Execute the fixed built-in adapter with a server-created `ToolExecutionContext`.
6. Validate normalized output against the output schema.
7. Complete the invocation and append `tool.completed` in one transaction.
8. Append a model-facing tool result message and continue the loop.

The following failures close the Run as `failed`:

- unbound or disabled tool: `tool_not_authorized`;
- malformed or schema-invalid arguments: `tool_invalid_arguments`;
- missing executor or invalid output: `tool_execution_failed`;
- more than four model iterations or eight calls: `tool_iteration_limit`;
- model transport or response error: existing `model_request_failed`.

The user receives a safe Chinese message. Invocation and Run records retain stable codes for diagnosis. No partial assistant message is persisted when the Run fails.

Normal agents with no bound tools retain the current one-request text behavior. Tool-free model providers receive no `tools` field.

## 9. API And Management UI

Add these APIs:

```text
GET   /api/tools
GET   /api/tools/{tool_id}
PATCH /api/tools/{tool_id}/toggle
GET   /api/agent-runs/{run_id}/tool-invocations
```

The first Tool Registry page is a quiet operational list under Capability management. It shows tool name, ID, version, source, risk, publication, enabled state, schema summary, and recent invocation counts. Built-in tools cannot be deleted or edited. Toggling requires administrator permission when real authentication replaces development identity; the first implementation follows the repository's current platform-management access pattern.

The Agent editor keeps the existing visual design. Its “模型与能力” tab adds a Tool picker separate from the Skill picker. Only published and enabled tools are selectable. A disabled bound tool remains visible with an unavailable state so administrators can repair the configuration.

The chat page maps `tool.started`, `tool.completed`, and `tool.failed` into compact Run activity rows. It shows display name, state, and duration. It does not show full arguments, output, stack traces, or credentials, and it does not render tool events as assistant messages.

## 10. Security Rules

- Tool authorization is the intersection of the concrete Run agent binding and current registry state.
- Tool IDs in model output are untrusted input.
- JSON Schema validation occurs before every execution; output validation occurs before results return to the model.
- Built-in dispatch is an explicit dictionary of known callables. No `eval`, dynamic import, subprocess, Shell, filesystem, or network API is allowed.
- `ToolExecutionContext` is constructed from server-side records and is not serializable into model arguments.
- Tool summaries are recursively redacted and length-bounded before persistence.
- Low-risk built-in tools execute without approval or sandbox allocation.
- A Prompt, Skill, MCP whitelist, frontend flag, or model response cannot expand the agent's Tool Registry authorization.
- Remote MCP, code, files, Shell, stdio, network tools, and device control remain closed until their gateway adapters, approval rules, and sandbox requirements are implemented.

## 11. Testing And Acceptance

Backend unit and integration coverage must prove:

- built-in tools initialize idempotently and default-agent bindings self-repair;
- agent create/update/copy preserves and validates `tool_ids`;
- disabled, unpublished, unknown, or unbound tools cannot execute;
- time output is deterministic under a frozen clock and handles valid and invalid IANA zones;
- runtime context uses persisted user, project, Conversation, and Run identity;
- input and output schema validation fail closed;
- invocation transitions and Run events are ordered and transactional;
- four model iterations and eight total calls are enforced;
- sensitive keys and oversized values are redacted or truncated;
- model adapters serialize tools and parse tool calls correctly;
- existing text-only agents and provider selection continue to work;
- tool failure terminates the Run with safe errors and no partial assistant message.

Frontend tests must prove:

- Tool Registry renders authoritative API state and protects built-ins;
- Agent management distinguishes Skill and Tool binding;
- chat Run activity reflects real Tool RunEvents;
- full arguments and results never render in activity rows;
- desktop and mobile layouts do not overlap or create page-level horizontal overflow.

End-to-end acceptance uses PostgreSQL and the configured default model:

1. Ask “今天星期几？”.
2. Verify the model requests `system.get_current_time`.
3. Verify one completed ToolInvocation and ordered Run events.
4. Verify the final answer matches the server date in `Asia/Shanghai`.
5. Remove the time-tool binding and repeat; verify the tool is unavailable and no invocation executes.
6. Rebind the tool, disable it platform-wide, and verify runtime authorization still fails closed.
7. Confirm credentials are absent from API responses, logs, RunEvents, and invocation summaries.

## 12. Deployment And Evolution

The migration seeds the two built-in definitions and repairs the built-in default agent binding. Deployment remains the existing unified Docker Compose platform with PostgreSQL, API, and Web; no new service or sandbox is required for this phase.

The next evolution publishes synchronized HTTP/SSE MCP tools into the same registry and adds an MCP execution adapter behind Tool Gateway. stdio MCP and script Skills remain blocked until the Action Sandbox exists. Knowledge retrieval, Artifact/GIS, Deep Agents, and LangGraph consume the same tool definitions, authorization, invocation audit, and RunEvent contracts rather than creating parallel execution paths.
