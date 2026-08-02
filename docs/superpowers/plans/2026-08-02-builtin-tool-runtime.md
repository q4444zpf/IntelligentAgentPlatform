# Built-in Tool Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为现有单智能体问答增加可审计、显式授权、具有调用上限的内置工具闭环，使默认智能体能够安全获取当前时间和可信运行上下文。

**Architecture:** 新建独立 `tools` 模块承担 Tool Registry、Tool Gateway、内置执行器和调用审计；扩展 OpenAI 兼容模型网关解析 `tool_calls`，由现有 `PlatformAgentHarness` 驱动最多 4 轮、最多 8 次调用的受控循环。智能体只获得显式绑定且当前已发布、已启用的工具，MCP、Skill、Shell、文件和沙箱执行继续关闭。

**Tech Stack:** FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, PostgreSQL, jsonschema, OpenAI-compatible Chat Completions, Vue 3, TypeScript, Ant Design Vue, Vitest, pytest.

---

## File Map

- Create `backend/alembic/versions/20260802_05_builtin_tool_runtime.py`: Tool Registry 与 ToolInvocation 表迁移。
- Modify `backend/app/db/platform_models.py`: `RegisteredToolRecord` ORM 模型。
- Modify `backend/app/conversations/models.py`: `ToolInvocation` ORM 模型。
- Create `backend/app/tools/schemas.py`: 工具、调用、执行上下文和值对象 Schema。
- Create `backend/app/tools/builtins.py`: 两个固定内置工具定义与执行器。
- Create `backend/app/tools/store.py`: 注册表与调用记录持久化。
- Create `backend/app/tools/service.py`: 初始化、查询、启停和授权解析。
- Create `backend/app/tools/gateway.py`: 参数/输出校验、调用限制、执行和脱敏。
- Create `backend/app/tools/router.py`: 工具注册表查询与启停 API。
- Modify `backend/app/main.py`: 注册工具路由。
- Modify `backend/app/conversations/router.py`: 项目/用户作用域内读取 Run 工具调用记录。
- Modify `backend/app/agents/schemas.py`: Agent 增加 `tool_ids`。
- Modify `backend/app/agents/service.py`: 默认绑定、自修复和工具校验。
- Modify `backend/app/runtime/model_gateway.py`: 工具定义、ToolCall 与 OpenAI 序列化/解析。
- Modify `backend/app/runtime/harness.py`: 受控 Tool Loop。
- Modify `backend/app/conversations/repository.py`: 读取 Run 上下文和调用记录。
- Modify `backend/app/conversations/dispatcher.py`: 注入 Tool Gateway。
- Modify `backend/requirements.txt`: 增加 JSON Schema 校验依赖。
- Create `backend/tests/tools/test_registry.py`: 注册表和内置初始化测试。
- Create `backend/tests/tools/test_gateway.py`: 授权、校验、执行、脱敏和限制测试。
- Create `backend/tests/tools/test_api.py`: 工具 API 与作用域测试。
- Modify `backend/tests/test_agents.py`: Agent 工具绑定和默认自修复测试。
- Modify `backend/tests/runtime/test_model_gateway.py`: OpenAI tools/tool_calls 契约测试。
- Modify `backend/tests/runtime/test_harness.py`: Tool Loop 和错误终态测试。
- Modify `backend/tests/conversations/test_dispatcher.py`: Dispatcher 依赖测试。
- Modify `backend/tests/integration/test_postgres_migrations.py`: PostgreSQL 迁移断言。
- Create `frontend/src/api/tools.ts`: 工具列表、启停和调用记录 API。
- Create `frontend/src/views/tools/ToolManageView.vue`: 工具注册中心页面。
- Create `frontend/src/views/tools/ToolManageView.test.ts`: 页面契约测试。
- Modify `frontend/src/api/agents.ts`: `tool_ids` 类型。
- Modify `frontend/src/views/agent/AgentManageView.vue`: 独立 Tool 选择器。
- Modify `frontend/src/views/agent/AgentManageView.test.ts`: Tool 绑定测试。
- Modify `frontend/src/router/routes.ts`: `/tools` 导航和路由。
- Modify `frontend/src/stores/permission.ts`: 工具查看权限。
- Modify `frontend/src/stores/conversations.ts`: Tool RunEvent 派生状态。
- Modify `frontend/src/views/agent/AgentConsoleView.vue`: 工具活动行。
- Modify `frontend/src/views/agent/AgentConsoleView.test.ts`: 工具事件展示和敏感字段隐藏测试。

### Task 1: Persist Tool Definitions And Invocations

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/app/db/platform_models.py`
- Modify: `backend/app/conversations/models.py`
- Create: `backend/alembic/versions/20260802_05_builtin_tool_runtime.py`
- Modify: `backend/tests/conversations/test_models.py`
- Modify: `backend/tests/integration/test_postgres_migrations.py`

- [ ] **Step 1: 写失败的 ORM 与迁移测试**

在 `backend/tests/conversations/test_models.py` 增加：

```python
def test_persists_tool_invocation_for_run(session, queued_run):
    invocation = ToolInvocation(
        run_id=queued_run.id,
        tool_call_id="call-time-1",
        tool_id="system.get_current_time",
        tool_version="1.0.0",
        status="started",
        arguments_summary={"timezone": "Asia/Shanghai"},
        result_summary={},
    )
    session.add(invocation)
    session.commit()
    saved = session.get(ToolInvocation, invocation.id)
    assert saved.tool_id == "system.get_current_time"
    assert saved.status == "started"
```

在 PostgreSQL 迁移测试中断言 `registered_tools`、`tool_invocations`、`ix_tool_invocations_run_id` 存在。

- [ ] **Step 2: 运行测试并确认失败**

Run:

```powershell
cd backend
pytest tests/conversations/test_models.py tests/integration/test_postgres_migrations.py -k tool -v
```

Expected: FAIL，原因是 ORM 模型和迁移尚不存在。

- [ ] **Step 3: 添加模型、迁移和依赖**

在 `backend/requirements.txt` 增加：

```text
jsonschema>=4.25.1,<5
```

`RegisteredToolRecord` 使用 `tool_id` 主键，字段为 `version`、`name`、`description`、`source`、`risk_level`、`input_schema`、`output_schema`、`requires_approval`、`published`、`enabled` 和时间戳。

`ToolInvocation` 使用 UUID 主键，`run_id` 外键 `agent_runs.id` 并启用级联删除；`tool_call_id` 与 `run_id` 建唯一约束；摘要字段使用 JSON；`duration_ms`、`completed_at` 允许为空。

迁移 revision 固定为：

```python
revision = "20260802_05"
down_revision = "20260801_04"
```

`downgrade()` 按索引、`tool_invocations`、`registered_tools` 的逆序删除。

- [ ] **Step 4: 运行模型和迁移测试**

Run: `cd backend; pytest tests/conversations/test_models.py tests/integration/test_postgres_migrations.py -v`

Expected: PASS；没有数据库前置条件时 PostgreSQL 用例按现有规则 SKIP。

- [ ] **Step 5: 提交持久化单元**

```bash
git add backend/requirements.txt backend/app/db/platform_models.py backend/app/conversations/models.py backend/alembic/versions/20260802_05_builtin_tool_runtime.py backend/tests/conversations/test_models.py backend/tests/integration/test_postgres_migrations.py
git commit -m "feat: persist tool registry and invocations"
```

### Task 2: Build Tool Registry And Management API

**Files:**
- Create: `backend/app/tools/__init__.py`
- Create: `backend/app/tools/schemas.py`
- Create: `backend/app/tools/builtins.py`
- Create: `backend/app/tools/store.py`
- Create: `backend/app/tools/service.py`
- Create: `backend/app/tools/router.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/tools/test_registry.py`
- Create: `backend/tests/tools/test_api.py`

- [ ] **Step 1: 写失败的注册表初始化和 API 测试**

覆盖：重复构造 Service 不重复、定义损坏时按内置定义修复、列表排序、读取、内置不可删除、启停、未知工具 404。

核心断言：

```python
def test_initializes_two_builtin_tools(client):
    response = client.get("/api/tools")
    assert response.status_code == 200
    tools = response.json()
    assert [item["tool_id"] for item in tools] == [
        "system.get_current_time",
        "system.get_runtime_context",
    ]
    assert all(item["source"] == "builtin" for item in tools)
    assert all(item["version"] == "1.0.0" for item in tools)
    assert all(item["published"] is True for item in tools)
```

- [ ] **Step 2: 运行聚焦测试并确认失败**

Run: `cd backend; pytest tests/tools/test_registry.py tests/tools/test_api.py -v`

Expected: FAIL，原因是 `app.tools` 和 `/api/tools` 不存在。

- [ ] **Step 3: 实现 Schema 和固定内置定义**

在 `schemas.py` 定义：

```python
ToolSource = Literal["builtin", "mcp", "knowledge", "artifact", "sandbox"]
ToolRisk = Literal["low", "medium", "high", "critical"]

class ToolInfo(BaseModel):
    tool_id: str
    version: str
    name: str
    description: str
    source: ToolSource
    risk_level: ToolRisk
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    requires_approval: bool
    published: bool
    enabled: bool
    is_builtin: bool
    created_at: datetime
    updated_at: datetime
```

`builtins.py` 暴露 `BUILTIN_TOOL_DEFINITIONS`，包含两个已确认的输入/输出 JSON Schema。定义使用纯字典常量，不包含执行逻辑导入路径。

- [ ] **Step 4: 实现 Store、Service 和路由**

`ToolStore` 提供 `list()`、`get(tool_id)`、`upsert_builtin(definition)`、`set_enabled(tool_id, enabled)`。

`ToolService.__init__()` 调用 `_ensure_builtins()`；每次修复只覆盖内置契约字段，保留管理员设置的 `enabled`。API：

```python
@router.get("", response_model=list[ToolInfo])
def list_tools(): ...

@router.get("/{tool_id}", response_model=ToolInfo)
def get_tool(tool_id: str): ...

@router.patch("/{tool_id}/toggle", response_model=ToolInfo)
def toggle_tool(tool_id: str): ...
```

在 `main.py` 注册 `prefix="/api/tools"`。普通字符串路径参数支持点号工具 ID，Service 仍执行完整格式校验。

- [ ] **Step 5: 运行注册表和 API 测试**

Run: `cd backend; pytest tests/tools/test_registry.py tests/tools/test_api.py tests/test_main.py -v`

Expected: PASS。

- [ ] **Step 6: 提交注册表单元**

```bash
git add backend/app/tools backend/app/main.py backend/tests/tools
git commit -m "feat: add builtin tool registry"
```

### Task 3: Bind Authorized Tools To Agents

**Files:**
- Modify: `backend/app/agents/schemas.py`
- Modify: `backend/app/agents/service.py`
- Modify: `backend/tests/test_agents.py`

- [ ] **Step 1: 写失败的 Agent 工具绑定测试**

新增用例：

```python
def test_builtin_default_repairs_required_tool_bindings(client):
    builtin = next(item for item in client.get("/api/agents").json()
                   if item["id"] == "platform-default-agent")
    assert builtin["tool_ids"] == [
        "system.get_current_time",
        "system.get_runtime_context",
    ]

def test_rejects_unknown_or_disabled_agent_tool(client):
    missing = client.post("/api/agents", json=agent_payload(
        tool_ids=["system.missing"],
    ))
    assert missing.status_code == 422
```

同时覆盖 update、copy、去重和“禁用后原绑定仍可读取但重新保存返回 422”。

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd backend; pytest tests/test_agents.py -k tool -v`

Expected: FAIL，原因是 Agent Schema 没有 `tool_ids`。

- [ ] **Step 3: 实现 Agent Schema 和校验**

在 `AgentConfig` 增加：

```python
tool_ids: list[str] = Field(default_factory=list, max_length=100)

@field_validator("tool_ids")
@classmethod
def normalize_tools(cls, value: list[str]) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in value if item.strip()))
```

`AgentService` 注入 `ToolService`。创建和更新时调用 `resolve_bindable(tool_ids)`；它要求工具存在、`published=True` 且 `enabled=True`。复制保留 `tool_ids`。

默认智能体配置增加两个工具 ID；`_ensure_default_agent()` 对旧配置做并集修复，不删除其他工具绑定。

- [ ] **Step 4: 运行完整 Agent 测试**

Run: `cd backend; pytest tests/test_agents.py -v`

Expected: PASS。

- [ ] **Step 5: 提交 Agent 授权单元**

```bash
git add backend/app/agents/schemas.py backend/app/agents/service.py backend/tests/test_agents.py
git commit -m "feat: bind registered tools to agents"
```

### Task 4: Implement Built-in Executors And Tool Gateway

**Files:**
- Modify: `backend/app/tools/schemas.py`
- Modify: `backend/app/tools/builtins.py`
- Modify: `backend/app/tools/store.py`
- Create: `backend/app/tools/gateway.py`
- Modify: `backend/app/conversations/repository.py`
- Create: `backend/tests/tools/test_gateway.py`

- [ ] **Step 1: 写失败的执行、安全和审计测试**

使用固定时钟覆盖：上海时间和星期、有效其他时区、非法时区、runtime context 服务端身份、未授权/禁用工具、额外参数、输出 Schema 错误、敏感摘要脱敏、长结果截断、重复 `tool_call_id`。

核心测试接口：

```python
context = ToolExecutionContext(
    run_id=run.id,
    conversation_id=conversation.id,
    project_id="project-1",
    user_id="user-1",
    timezone="Asia/Shanghai",
)
result = gateway.execute(
    context=context,
    authorized_tool_ids={"system.get_current_time"},
    tool_call=ToolCall(
        id="call-1",
        name="system.get_current_time",
        arguments={"timezone": "Asia/Shanghai"},
    ),
)
assert result.output["weekday_zh"] == "星期日"
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd backend; pytest tests/tools/test_gateway.py -v`

Expected: FAIL，原因是 Gateway 和执行值对象不存在。

- [ ] **Step 3: 实现执行上下文和固定执行器**

`ToolExecutionContext` 为 frozen dataclass，只接受服务端字段。`BUILTIN_EXECUTORS` 是：

```python
BUILTIN_EXECUTORS = {
    "system.get_current_time": get_current_time,
    "system.get_runtime_context": get_runtime_context,
}
```

`schemas.py` 同时定义后续 Model Gateway 和 Tool Gateway 共用的不可变值对象：

```python
@dataclass(frozen=True)
class ToolDefinition:
    tool_id: str
    description: str
    input_schema: dict[str, Any]

@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

@dataclass(frozen=True)
class ToolExecutionContext:
    run_id: str
    conversation_id: str
    project_id: str
    user_id: str
    timezone: str = "Asia/Shanghai"
```
时间工具使用 `ZoneInfo`；星期中文映射固定为 `星期一` 至 `星期日`。runtime context 忽略模型参数，只读取 context。

- [ ] **Step 4: 实现 Gateway、Schema 校验和审计**

使用 `jsonschema.Draft202012Validator` 校验输入和输出。执行步骤按设计中的事务边界写入 invocation 与 `tool.started/completed/failed`。

Gateway 对外只抛：

```python
class ToolRuntimeError(Exception):
    code: str
    safe_message: str
```

摘要递归遮蔽敏感 key，列表最多 20 项、对象最多 50 个 key、深度最多 5、最终 JSON 最多 4096 字符。

- [ ] **Step 5: 运行 Gateway 和 Repository 测试**

Run: `cd backend; pytest tests/tools/test_gateway.py tests/conversations/test_repository.py -v`

Expected: PASS。

- [ ] **Step 6: 提交 Tool Gateway**

```bash
git add backend/app/tools backend/app/conversations/repository.py backend/tests/tools/test_gateway.py backend/tests/conversations/test_repository.py
git commit -m "feat: execute audited builtin tools"
```

### Task 5: Extend The Model Gateway For Tool Calls

**Files:**
- Modify: `backend/app/runtime/model_gateway.py`
- Modify: `backend/tests/runtime/test_model_gateway.py`

- [ ] **Step 1: 写失败的模型协议测试**

覆盖无工具时不发送 `tools`、有工具时发送 function Schema、解析单个/多个 ToolCall、保留 call ID、非法 JSON、空 content 且无 calls、工具消息序列化，以及响应/密钥不进入异常。

期望调用：

```python
result = gateway.generate(
    [{"role": "user", "content": "今天星期几？"}],
    ModelSelection("deepseek", "deepseek-chat"),
    tools=[ToolDefinition(
        tool_id="system.get_current_time",
        description="获取当前时间",
        input_schema={"type": "object", "properties": {}},
    )],
)
assert result.tool_calls == (
    ToolCall(id="call-1", name="system.get_current_time", arguments={}),
)
```

- [ ] **Step 2: 运行模型网关测试并确认失败**

Run: `cd backend; pytest tests/runtime/test_model_gateway.py -v`

Expected: FAIL，原因是 Gateway 仍是 content-only 契约。

- [ ] **Step 3: 实现结构化模型值对象**

`ModelResult.content` 改为 `str | None`，新增不可变 `tool_calls: tuple[ToolCall, ...] = ()`。`ToolDefinition` 和 `ToolCall` 放在 `app.tools.schemas`，模型网关直接复用。

`generate(messages, selection=None, tools=None)` 仅在 tools 非空时添加：

```python
payload["tools"] = [{
    "type": "function",
    "function": {
        "name": tool.tool_id,
        "description": tool.description,
        "parameters": tool.input_schema,
    },
} for tool in tools]
payload["tool_choice"] = "auto"
```

解析 `message.tool_calls` 的 function name 和 arguments；arguments 必须是 JSON object。

- [ ] **Step 4: 运行模型网关及 Dispatcher 测试**

Run: `cd backend; pytest tests/runtime/test_model_gateway.py tests/conversations/test_dispatcher.py -v`

Expected: PASS。

- [ ] **Step 5: 提交模型协议**

```bash
git add backend/app/runtime/model_gateway.py backend/app/tools/schemas.py backend/tests/runtime/test_model_gateway.py backend/tests/conversations/test_dispatcher.py
git commit -m "feat: support model tool calls"
```

### Task 6: Run The Bounded Tool Loop

**Files:**
- Modify: `backend/app/runtime/harness.py`
- Modify: `backend/app/conversations/repository.py`
- Modify: `backend/app/conversations/dispatcher.py`
- Modify: `backend/tests/runtime/test_harness.py`
- Modify: `backend/tests/conversations/test_dispatcher.py`

- [ ] **Step 1: 写失败的 Harness Tool Loop 测试**

覆盖：时间工具一次调用后生成最终答复、无工具保持一次调用、多个工具顺序执行、未绑定工具失败、禁用工具失败、4 轮/8 次上限、工具失败不写 assistant 消息、事件顺序、具体 Run 上下文。

核心断言：

```python
assert [event.event_type for event in repository.list_events(run_id, 0)] == [
    "run.status",
    "run.status",
    "tool.started",
    "tool.completed",
    "message.completed",
    "run.usage",
    "run.status",
]
```

- [ ] **Step 2: 运行 Harness 测试并确认失败**

Run: `cd backend; pytest tests/runtime/test_harness.py -k tool -v`

Expected: FAIL，原因是 Harness 仍只请求一次模型。

- [ ] **Step 3: 实现受控循环**

在 Harness 中固定：

```python
MAX_MODEL_ITERATIONS = 4
MAX_TOOL_CALLS = 8
```

从具体 Agent 的 `tool_ids` 解析授权定义；每次模型返回 ToolCall 时先追加完整 assistant tool-call 内存消息，再逐个调用 Gateway，并追加 `role=tool`、`tool_call_id` 和规范 JSON 内容。只有拿到非空最终 content 才写 assistant Message。

聚合每轮 token usage；达到上限抛 `ToolRuntimeError("tool_iteration_limit", "工具调用次数超过平台限制")`。

- [ ] **Step 4: 注入服务端运行上下文**

Repository 增加 `get_run_execution_context(run_id)`，通过 Run JOIN Conversation 返回 `owner_id`、`project_id`、Conversation ID 和 Run ID。Dispatcher 使用同一 SessionFactory 创建 ToolService、ToolStore 和 ToolGateway，禁止构造第二套数据库身份。

- [ ] **Step 5: 运行运行时与会话测试**

Run: `cd backend; pytest tests/runtime tests/conversations/test_dispatcher.py tests/conversations/test_api.py -v`

Expected: PASS。

- [ ] **Step 6: 提交运行时闭环**

```bash
git add backend/app/runtime/harness.py backend/app/conversations/repository.py backend/app/conversations/dispatcher.py backend/tests/runtime/test_harness.py backend/tests/conversations/test_dispatcher.py
git commit -m "feat: execute bounded agent tool loop"
```

### Task 7: Expose Invocation History And Build Tool Management UI

**Files:**
- Modify: `backend/app/conversations/router.py`
- Modify: `backend/tests/tools/test_api.py`
- Create: `frontend/src/api/tools.ts`
- Create: `frontend/src/views/tools/ToolManageView.vue`
- Create: `frontend/src/views/tools/ToolManageView.test.ts`
- Modify: `frontend/src/router/routes.ts`
- Modify: `frontend/src/stores/permission.ts`

- [ ] **Step 1: 写失败的调用记录 API 与前端契约测试**

API 测试断言其他项目或用户读取 Run 调用记录返回 404；合法上下文按创建时间返回脱敏 `ToolInvocationInfo`。

前端 source-contract 测试断言：

```typescript
expect(source).toContain('toolsApi.list')
expect(source).toContain('系统内置')
expect(source).toContain('riskLabel')
expect(source).not.toContain('DeleteOutlined')
expect(routesSource).toContain("path: '/tools'")
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd backend; pytest tests/tools/test_api.py -v`

Run: `cd frontend; npm test -- src/views/tools/ToolManageView.test.ts`

Expected: FAIL，原因是调用记录 API 和页面不存在。

- [ ] **Step 3: 实现调用记录 API**

在现有 Conversation router 中增加路由并复用 RequestContext：

```python
@router.get("/agent-runs/{run_id}/tool-invocations",
            response_model=list[ToolInvocationInfo])
def list_run_tool_invocations(
    run_id: str,
    context: RequestContext = Depends(get_request_context),
): ...
```

Service 先通过 scoped ConversationRepository 验证 Run 所有权，再读取调用记录。

- [ ] **Step 4: 实现工具页面和导航**

新增 `/tools`，能力菜单将原“Skill / Tool 管理”改为独立“Skill 管理”和“工具注册中心”。页面沿用 MCP/Skill 页面视觉规范，只展示列表、筛选、风险、Schema 摘要和启停；不提供新增、编辑、删除或执行按钮。

- [ ] **Step 5: 运行 API、前端测试和构建**

Run: `cd backend; pytest tests/tools/test_api.py -v`

Run: `cd frontend; npm test -- src/views/tools/ToolManageView.test.ts && npm run build`

Expected: PASS。

- [ ] **Step 6: 提交管理界面**

```bash
git add backend/app/conversations/router.py backend/tests/tools/test_api.py frontend/src/api/tools.ts frontend/src/views/tools frontend/src/router/routes.ts frontend/src/stores/permission.ts
git commit -m "feat: manage registered tools"
```

### Task 8: Add Agent Tool Picker And Chat Activity

**Files:**
- Modify: `frontend/src/api/agents.ts`
- Modify: `frontend/src/views/agent/AgentManageView.vue`
- Modify: `frontend/src/views/agent/AgentManageView.test.ts`
- Modify: `frontend/src/stores/conversations.ts`
- Modify: `frontend/src/views/agent/AgentConsoleView.vue`
- Modify: `frontend/src/views/agent/AgentConsoleView.test.ts`
- Modify: `frontend/src/stores/conversations.test.ts`

- [ ] **Step 1: 写失败的 Agent Tool 选择和活动事件测试**

断言 Agent editor 调用 `toolsApi.list`、只选择 published/enabled 工具、提交 `tool_ids`，并在 Skill 区域下方独立显示“授权工具”。

Store 测试输入：

```typescript
[
  { sequence: 2, event_type: 'tool.started', payload: {
    invocation_id: 'i1', tool_id: 'system.get_current_time',
    display_name: '获取当前时间',
  } },
  { sequence: 3, event_type: 'tool.completed', payload: {
    invocation_id: 'i1', tool_id: 'system.get_current_time',
    display_name: '获取当前时间', duration_ms: 4,
  } },
]
```

断言 UI 显示名称、完成和 `4 ms`，不渲染 `arguments_summary`、`result_summary` 或任意 `secret` 文本。

- [ ] **Step 2: 运行聚焦测试并确认失败**

Run:

```powershell
cd frontend
npm test -- src/views/agent/AgentManageView.test.ts src/views/agent/AgentConsoleView.test.ts src/stores/conversations.test.ts
```

Expected: FAIL，原因是前端尚未读取工具和派生活动。

- [ ] **Step 3: 实现 Agent Tool picker**

`AgentInput` 增加 `tool_ids: string[]`。`loadAgents()` 并行读取 agents、providers、skills、tools。新增 Tool picker 使用复选卡片，显示名称、ID、风险和来源；disabled bound tool 只读展示“不可用”。不修改现有整体布局和样式方向。

- [ ] **Step 4: 实现聊天工具活动**

Store 以 `invocation_id` 归并事件，暴露按 sequence 排序的 `toolActivities`。聊天页在运行中的 thinking 行附近显示紧凑活动列表；只消费白名单字段 `display_name`、状态和 `duration_ms`。

- [ ] **Step 5: 运行完整前端测试和构建**

Run: `cd frontend; npm test`

Expected: 所有测试 PASS。

Run: `cd frontend; npm run build`

Expected: vue-tsc 和 Vite build PASS。

- [ ] **Step 6: 提交 Agent 和聊天 UI**

```bash
git add frontend/src/api/agents.ts frontend/src/views/agent/AgentManageView.vue frontend/src/views/agent/AgentManageView.test.ts frontend/src/stores/conversations.ts frontend/src/stores/conversations.test.ts frontend/src/views/agent/AgentConsoleView.vue frontend/src/views/agent/AgentConsoleView.test.ts
git commit -m "feat: configure and display agent tools"
```

### Task 9: Full Verification And Production Acceptance

**Verification scope:** 本任务不预先修改源文件。若发现缺陷，先在所属模块增加能够复现问题的失败测试，再做最小修复并重新运行完整验证。

- [ ] **Step 1: 运行完整后端测试**

Run: `cd backend; pytest -q`

Expected: PASS；PostgreSQL 外部前置条件缺失时只允许现有迁移用例 SKIP。

- [ ] **Step 2: 运行完整前端测试和构建**

Run: `cd frontend; npm test`

Run: `cd frontend; npm run build`

Expected: PASS。

- [ ] **Step 3: 重建统一部署环境**

```powershell
$env:IAP_ALLOW_DEV_IDENTITY='true'
$env:VITE_DEV_USER_ID='dev-user'
$env:VITE_DEV_PROJECT_ID='dev-project'
docker compose up -d --build --force-recreate api web
docker compose ps
```

Expected: PostgreSQL 和 API healthy，Web running。

- [ ] **Step 4: 执行真实模型验收**

在 `/chat/focus` 发送“今天星期几？”，记录：

- Run ID；
- `actor_id=platform-default-agent`；
- 一个 completed 的 `system.get_current_time` 调用；
- `tool.started` 在 `tool.completed` 前；
- 最终回答与 `Asia/Shanghai` 服务端日期一致。

- [ ] **Step 5: 执行授权失败验收**

创建临时 Agent，不绑定时间工具并显式选择它发送同一问题；确认没有 ToolInvocation。再绑定工具并平台禁用；确认 Gateway 拒绝执行、Run 安全失败。最后恢复工具、默认智能体并删除临时 Agent。

- [ ] **Step 6: 执行安全和响应式检查**

检查 API、最近容器日志、RunEvents 和 invocation summaries 不含未脱敏密钥；使用 1440x900、390x844 检查 `/tools`、`/agent/manage`、`/chat/focus` 无重叠和页面级横向溢出，浏览器控制台无 error。

- [ ] **Step 7: 核对工作区并提交必要的验收修复**

Run: `git status --short`

Expected: 仅保留实施前已存在的未跟踪缓存目录；不得提交 `.pnpm-store`、pytest 临时目录、凭据、测试会话导出或模型响应正文。
