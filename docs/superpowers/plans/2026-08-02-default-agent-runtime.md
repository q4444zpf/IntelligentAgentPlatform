# Default Agent Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a protected built-in fallback agent, an administrator-selectable platform default agent, and make the selected agent's model and prompts drive Web chat runs.

**Architecture:** Reuse `managed_agents` for agent configuration and `platform_settings` for the versioned `default_agent` pointer. Resolve every chat request to a concrete enabled agent before persisting its Run, then have the Harness load that same configuration and pass an explicit model selection plus layered prompts to the model gateway.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy 2, PostgreSQL/SQLite tests, Vue 3, TypeScript, Ant Design Vue, Vitest, pytest.

---

## File Map

- Modify `backend/app/agents/schemas.py`: expose default/built-in state and default-switch request.
- Modify `backend/app/agents/store.py`: persist and compare-and-swap the `default_agent` platform setting.
- Modify `backend/app/agents/service.py`: initialize the fallback agent and enforce lifecycle protection.
- Modify `backend/app/agents/router.py`: expose default lookup/switch endpoints and conflict responses.
- Modify `backend/tests/test_agents.py`: cover initialization, switching, protection and recovery.
- Modify `backend/app/conversations/schemas.py`: make agent selection optional for single-agent requests.
- Modify `backend/app/conversations/service.py`: resolve and validate the concrete agent before Run creation.
- Modify `backend/tests/conversations/test_service.py` and `backend/tests/conversations/test_api.py`: cover implicit and explicit selection.
- Modify `backend/app/runtime/model_gateway.py`: accept an optional explicit provider/model selection.
- Modify `backend/app/runtime/harness.py`: load the Run agent and compose prompts.
- Modify `backend/tests/runtime/test_model_gateway.py` and `backend/tests/runtime/test_harness.py`: cover selection, prompt order and failures.
- Modify `frontend/src/api/agents.ts`: add default flags and endpoints.
- Modify `frontend/src/views/agent/AgentManageView.vue`: add badges, switch action and protected controls.
- Create `frontend/src/views/agent/AgentManageView.test.ts`: verify management contracts.
- Modify `frontend/src/api/conversations.ts`: permit omitted agent ID.
- Modify `frontend/src/views/agent/AgentConsoleView.vue`: load real agents and follow backend default.
- Modify `frontend/src/views/agent/AgentConsoleView.test.ts`: remove hardcoded-agent expectations.

### Task 1: Default Agent Persistence And Initialization

**Files:**
- Modify: `backend/app/agents/schemas.py`
- Modify: `backend/app/agents/store.py`
- Modify: `backend/app/agents/service.py`
- Test: `backend/tests/test_agents.py`

- [ ] **Step 1: Write failing initialization and protection-state tests**

Add tests that construct `AgentService`, call `list()`, and assert exactly one `platform-default-agent` with `is_builtin is True`, `is_default is True`, and `enabled is True`. Reconstruct the service against the same database and assert no duplicate row. Directly corrupt `default_agent` to a missing ID and assert `get_default()` repairs it to the built-in ID.

```python
def test_initializes_one_builtin_default_agent(client):
    agents = client.get("/api/agents").json()
    builtin = [item for item in agents if item["id"] == "platform-default-agent"]
    assert len(builtin) == 1
    assert builtin[0]["is_builtin"] is True
    assert builtin[0]["is_default"] is True
    assert builtin[0]["enabled"] is True
```

- [ ] **Step 2: Run the focused test and verify failure**

Run: `cd backend; pytest tests/test_agents.py::test_initializes_one_builtin_default_agent -v`

Expected: FAIL because no built-in agent or flags exist.

- [ ] **Step 3: Add schemas and versioned store operations**

Add `is_builtin: bool`, `is_default: bool` to `AgentInfo`, plus:

```python
class AgentDefaultRequest(BaseModel):
    agent_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
```

In `AgentStore`, add `DEFAULT_SETTING_KEY = "default_agent"`, `get_default_id()`, and `set_default_id(agent_id, expected_version=None)`. Store `{ "agent_id": agent_id, "scope": "platform" }` in `PlatformSettingRecord`; use its `version` in an SQLAlchemy `update(...)` predicate and raise `AgentConcurrentUpdateError` when `rowcount != 1`.

- [ ] **Step 4: Implement idempotent built-in initialization**

Add constants and call `_ensure_default_agent()` before public reads/mutations:

```python
BUILTIN_AGENT_ID = "platform-default-agent"
BUILTIN_CONFIG = AgentConfig(
    name="平台默认智能体",
    description="面向水利业务问答与平台通用操作的默认智能体",
    runtime_form="web",
    system_prompt="你是专业、审慎的水利智能体平台助手。仅使用平台授权的能力回答和执行任务。",
    context_prompt="结合当前项目、页面和对话中经过平台验证的上下文回答。",
    approval_policy="control_commands",
    enabled=True,
)
```

If the built-in row is absent, create its workspace and record. If the pointer is empty, missing, or points to a disabled record, set it to the built-in ID. If historical data disabled the built-in record, restore `enabled=True`. Extend `_info()` to compute flags server-side.

- [ ] **Step 5: Run all agent tests**

Run: `cd backend; pytest tests/test_agents.py -v`

Expected: PASS, including existing create/update/copy tests updated to account for the automatically listed built-in agent.

- [ ] **Step 6: Commit the persistence unit**

```bash
git add backend/app/agents/schemas.py backend/app/agents/store.py backend/app/agents/service.py backend/tests/test_agents.py
git commit -m "feat: initialize protected default agent"
```

### Task 2: Default Agent Management API And Protection

**Files:**
- Modify: `backend/app/agents/router.py`
- Modify: `backend/app/agents/service.py`
- Test: `backend/tests/test_agents.py`

- [ ] **Step 1: Write failing API behavior tests**

Cover `GET /api/agents/default`, `PUT /api/agents/default`, rejecting disabled targets, rejecting deletion of the built-in agent, and rejecting deletion or disabling of the active default. After switching away, assert the old ordinary default may be disabled/deleted while the built-in remains undeletable.

```python
def test_switches_default_only_to_enabled_agent(client):
    client.post("/api/agents", json=agent_payload())
    response = client.put("/api/agents/default", json={"agent_id": "reservoir-dispatch"})
    assert response.status_code == 200
    assert response.json()["is_default"] is True
    assert client.delete("/api/agents/reservoir-dispatch").status_code == 409
```

- [ ] **Step 2: Verify the new tests fail**

Run: `cd backend; pytest tests/test_agents.py -k "default or builtin" -v`

Expected: FAIL because routes and lifecycle checks are absent.

- [ ] **Step 3: Implement service rules and API routes**

Add `AgentProtectedError`, `get_default()`, and `set_default(agent_id)`. In `set_enabled()` reject `enabled=False` for the current default. In `delete()` reject the built-in and current default before calling the store.

Register static routes before `/{agent_id}`:

```python
@router.get("/default", response_model=AgentInfo)
def get_default_agent():
    return call(manager.get_default)

@router.put("/default", response_model=AgentInfo)
def set_default_agent(request: AgentDefaultRequest):
    return call(lambda: manager.set_default(request.agent_id))
```

Map `AgentProtectedError` and concurrent update errors to HTTP 409; retain 404 for a missing target and 422 for a disabled target.

- [ ] **Step 4: Run agent tests and verify pass**

Run: `cd backend; pytest tests/test_agents.py -v`

Expected: PASS.

- [ ] **Step 5: Commit the management API**

```bash
git add backend/app/agents/router.py backend/app/agents/service.py backend/tests/test_agents.py
git commit -m "feat: manage platform default agent"
```

### Task 3: Resolve A Concrete Agent For Every Chat Run

**Files:**
- Modify: `backend/app/conversations/schemas.py`
- Modify: `backend/app/conversations/service.py`
- Modify: `backend/app/conversations/router.py`
- Test: `backend/tests/conversations/test_service.py`
- Test: `backend/tests/conversations/test_api.py`

- [ ] **Step 1: Write failing service and API tests**

Inject an `AgentService` backed by the test database. Assert an `actor_type="agent"` message with omitted `actor_id` persists the current default's concrete ID. Assert an explicit enabled ID is preserved. Assert missing/disabled explicit IDs return 422 and no user message or Run is committed.

```python
request = MessageCreate(content="分析洪峰", actor_type="agent")
accepted = service.create_message(context, conversation.id, request)
assert accepted.run.actor_id == "platform-default-agent"
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `cd backend; pytest tests/conversations/test_service.py tests/conversations/test_api.py -k agent -v`

Expected: FAIL because `actor_id` is required and not validated.

- [ ] **Step 3: Implement request resolution before persistence**

Change `MessageCreate.actor_id` to `str | None = None`. Add `AgentSelectionError`. Inject `AgentService` into `ConversationService`, then resolve before adding the message:

```python
def _resolve_actor_id(self, request: MessageCreate) -> str:
    if request.actor_type == "team":
        if not request.actor_id:
            raise AgentSelectionError("A team must be selected")
        return request.actor_id
    agent = self.agent_service.get(request.actor_id) if request.actor_id else self.agent_service.get_default()
    if not agent.enabled:
        raise AgentSelectionError("The selected agent is disabled")
    return agent.id
```

Persist `actor_id=resolved_actor_id`. Update the router factory to provide `AgentService()` and map selection errors to 422 without leaking internals.

- [ ] **Step 4: Run conversation tests**

Run: `cd backend; pytest tests/conversations -v`

Expected: PASS.

- [ ] **Step 5: Commit concrete actor resolution**

```bash
git add backend/app/conversations/schemas.py backend/app/conversations/service.py backend/app/conversations/router.py backend/tests/conversations/test_service.py backend/tests/conversations/test_api.py
git commit -m "feat: resolve default agent for chat runs"
```

### Task 4: Apply Agent Model And Prompts At Runtime

**Files:**
- Modify: `backend/app/runtime/model_gateway.py`
- Modify: `backend/app/runtime/harness.py`
- Modify: `backend/app/conversations/dispatcher.py`
- Test: `backend/tests/runtime/test_model_gateway.py`
- Test: `backend/tests/runtime/test_harness.py`
- Test: `backend/tests/conversations/test_dispatcher.py`

- [ ] **Step 1: Write failing gateway-selection and prompt-order tests**

Change fake gateways to capture `provider_id` and `model`. Seed the Run actor in `managed_agents`. Assert the gateway receives platform identity first internally, while Harness input is ordered as agent system prompt, context prompt, conversation messages. Add fallback assertions when agent model fields are empty and failure assertions for missing/disabled Run actors.

```python
assert gateway.calls == [{
    "messages": [
        {"role": "system", "content": "你是水库调度专家。"},
        {"role": "system", "content": "结合当前页面对象回答。"},
        {"role": "user", "content": "分析洪峰"},
    ],
    "provider_id": "deepseek",
    "model": "deepseek-chat",
}]
```

- [ ] **Step 2: Run focused runtime tests and verify failure**

Run: `cd backend; pytest tests/runtime/test_harness.py tests/runtime/test_model_gateway.py -v`

Expected: FAIL because Harness ignores agent configuration and the gateway has no explicit selection parameters.

- [ ] **Step 3: Extend the gateway contract**

Use a request value object instead of positional ambiguity:

```python
@dataclass(frozen=True)
class ModelSelection:
    provider_id: str = ""
    model: str = ""

class ModelGateway(Protocol):
    def generate(self, messages: list[dict[str, str]], selection: ModelSelection | None = None) -> ModelResult: ...
```

In `OpenAICompatibleModelGateway.generate`, resolve the explicit pair only when both fields are populated; otherwise call `ProviderService.get_active()`. Validate configured provider, enabled model and protocol through one shared helper. Keep `build_runtime_messages()` responsible for the authoritative model identity so it stays ahead of agent prompts.

- [ ] **Step 4: Load and apply the Run agent in Harness**

Inject `AgentService` into `PlatformAgentHarness`. Before setting `running`, load `run.actor_id`; fail with `agent_unavailable` if missing or disabled. Build non-empty prompt messages in this order:

```python
agent_messages = [
    *([{"role": "system", "content": agent.system_prompt}] if agent.system_prompt.strip() else []),
    *([{"role": "system", "content": agent.context_prompt}] if agent.context_prompt.strip() else []),
    *conversation_messages,
]
selection = ModelSelection(agent.provider_id, agent.model)
result = self.model_gateway.generate(agent_messages, selection)
```

Update `ThreadRunDispatcher` to construct an AgentService using the same session factory/store boundary as the worker. Ensure errors become safe Run events and never include stored credentials or prompt internals.

- [ ] **Step 5: Run runtime and dispatcher tests**

Run: `cd backend; pytest tests/runtime tests/conversations/test_dispatcher.py -v`

Expected: PASS.

- [ ] **Step 6: Commit runtime integration**

```bash
git add backend/app/runtime/model_gateway.py backend/app/runtime/harness.py backend/app/conversations/dispatcher.py backend/tests/runtime/test_model_gateway.py backend/tests/runtime/test_harness.py backend/tests/conversations/test_dispatcher.py
git commit -m "feat: apply agent configuration at runtime"
```

### Task 5: Add Default-Agent Management Controls

**Files:**
- Modify: `frontend/src/api/agents.ts`
- Modify: `frontend/src/views/agent/AgentManageView.vue`
- Create: `frontend/src/views/agent/AgentManageView.test.ts`

- [ ] **Step 1: Write a failing source-contract test**

Create tests that assert the view renders `平台默认` and `系统内置`, calls `agentsApi.setDefault`, and guards delete/toggle using `is_default`/`is_builtin` rather than only visual text.

```typescript
expect(source).toContain('agentsApi.setDefault');
expect(source).toContain("agent.is_default");
expect(source).toContain("agent.is_builtin");
expect(source).toContain('平台默认');
```

- [ ] **Step 2: Run the test and verify failure**

Run: `cd frontend; npm test -- src/views/agent/AgentManageView.test.ts`

Expected: FAIL because API flags and controls do not exist.

- [ ] **Step 3: Extend the API types and methods**

Add `is_builtin` and `is_default` to `AgentInfo`, plus:

```typescript
getDefault: () => request<AgentInfo>('/agents/default'),
setDefault: (agentId: string) => request<AgentInfo>('/agents/default', {
  method: 'PUT',
  ...json({ agent_id: agentId }),
}),
```

- [ ] **Step 4: Implement management interactions without restyling the page**

Add compact tags beside the agent name. Add a `设为默认` command only when `agent.enabled && !agent.is_default`. Disable delete when `agent.is_builtin || agent.is_default`; disable the switch that would turn off a default agent. Add title/tooltips explaining `系统内置智能体不能删除` and `平台默认智能体不能停用或删除`. On switch success, reload the list and show `平台默认智能体已更新`.

- [ ] **Step 5: Run management tests and frontend build**

Run: `cd frontend; npm test -- src/views/agent/AgentManageView.test.ts`

Expected: PASS.

Run: `cd frontend; npm run build`

Expected: TypeScript check and Vite build succeed.

- [ ] **Step 6: Commit management UI**

```bash
git add frontend/src/api/agents.ts frontend/src/views/agent/AgentManageView.vue frontend/src/views/agent/AgentManageView.test.ts
git commit -m "feat: manage default agent in console"
```

### Task 6: Use Real Agents In Web Chat And Verify End To End

**Files:**
- Modify: `frontend/src/api/conversations.ts`
- Modify: `frontend/src/views/agent/AgentConsoleView.vue`
- Modify: `frontend/src/views/agent/AgentConsoleView.test.ts`

- [ ] **Step 1: Write failing chat selection tests**

Replace the hardcoded `flood` contract with assertions that the view calls `agentsApi.list`, derives the default from `is_default`, only lists enabled Web/Common agents, and can omit `actor_id` while the default list is loading.

```typescript
expect(source).toContain('agentsApi.list');
expect(source).toContain('agent.is_default');
expect(source).not.toContain("ref('flood')");
```

- [ ] **Step 2: Run the focused test and verify failure**

Run: `cd frontend; npm test -- src/views/agent/AgentConsoleView.test.ts`

Expected: FAIL on the hardcoded selector and missing API loading.

- [ ] **Step 3: Implement default-aware chat selection**

Change the conversation request type to `actor_id?: string`. Load agents on mount, filter `enabled && (runtime_form === 'web' || runtime_form === 'common')`, and initialize `selectedAgentId` from `is_default`. Preserve a user's explicit selection during later refreshes. If no list value is available, send single-agent messages without `actor_id` so the backend remains authoritative. Keep team mode disabled until its runtime exists.

- [ ] **Step 4: Run all frontend tests and build**

Run: `cd frontend; npm test`

Expected: all tests PASS.

Run: `cd frontend; npm run build`

Expected: PASS with no Vue or TypeScript errors.

- [ ] **Step 5: Run the complete backend suite**

Run: `cd backend; pytest -q`

Expected: all tests PASS; the existing environment-dependent PostgreSQL migration test may remain skipped only when its documented database prerequisite is absent.

- [ ] **Step 6: Run migration and browser smoke checks**

Run: `docker compose up -d --build postgres api web`

Expected: all three services become healthy/running. Preserve the approved local development identity environment variables when recreating containers.

Open `/agent/manage`, verify one built-in/default row, switch a second enabled agent to default, confirm protected actions, then open `/chat/focus`, send a message without manually changing the selector, and verify the resulting Run stores the new default ID and returns a model response.

- [ ] **Step 7: Commit chat integration**

```bash
git add frontend/src/api/conversations.ts frontend/src/views/agent/AgentConsoleView.vue frontend/src/views/agent/AgentConsoleView.test.ts
git commit -m "feat: use platform default agent in chat"
```

- [ ] **Step 8: Record final evidence**

Run: `git status --short`

Expected: no task-created uncommitted changes. Pre-existing unrelated changes must be preserved and reported separately rather than included or reverted.

Record the backend test count, frontend test count, build result, container health, selected default agent ID, and one successful Run ID in the completion report. Do not record API keys, authorization headers, or full provider responses.
