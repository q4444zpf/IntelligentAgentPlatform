# Minimal Agent Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将已入库的 AgentRun 交给平台默认模型执行，持久化助手消息与运行事件，并让 Web 问答自动取得最终结果。

**Architecture:** `PlatformAgentHarness` 负责状态机和数据库写入，`OpenAICompatibleModelGateway` 负责解析当前默认模型并调用上游，`ThreadRunDispatcher` 使用独立 SQLAlchemy Session 在请求提交后执行。前端沿用现有 SSE 增量接口进行有限间隔轮询，终态后刷新消息。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、httpx、pytest、Vue 3、Pinia、TypeScript、Vitest

---

## 文件结构

- Create `backend/app/runtime/__init__.py`: 运行时包出口。
- Create `backend/app/runtime/model_gateway.py`: 默认模型解析、上游请求和标准化响应。
- Create `backend/app/runtime/harness.py`: Run 状态机、对话上下文、消息和事件持久化。
- Modify `backend/app/conversations/repository.py`: 增加 Harness 所需的执行上下文和写入方法。
- Modify `backend/app/conversations/dispatcher.py`: 增加独立 Session 的线程调度器。
- Modify `backend/app/conversations/router.py`: 默认服务工厂接入真实 Dispatcher。
- Create `backend/tests/runtime/test_model_gateway.py`: 模型网关行为测试。
- Create `backend/tests/runtime/test_harness.py`: 成功和失败运行闭环测试。
- Create `backend/tests/conversations/test_dispatcher.py`: 后台独立会话调度测试。
- Modify `frontend/src/stores/conversations.ts`: 轮询终态并刷新消息。
- Modify `frontend/src/stores/conversations.test.ts`: 完成、失败和轮询停止测试。

### Task 1: 默认模型网关

**Files:**
- Create: `backend/app/runtime/__init__.py`
- Create: `backend/app/runtime/model_gateway.py`
- Test: `backend/tests/runtime/test_model_gateway.py`

- [ ] **Step 1: 写默认模型选择和 OpenAI-compatible 请求的失败测试**

测试使用临时 ProviderStore 和本地 HTTPServer，配置并激活模型后调用：

```python
result = gateway.generate([{"role": "user", "content": "分析洪峰"}])
assert result.content == "研判完成"
assert captured["model"] == "deepseek-chat"
assert captured["authorization"] == "Bearer runtime-secret"
assert result.total_tokens == 18
```

另写测试验证无默认模型时抛出 `ModelConfigurationError`，异常不包含 API Key。

- [ ] **Step 2: 运行测试并确认按预期失败**

Run: `python -m pytest backend/tests/runtime/test_model_gateway.py -v`

Expected: FAIL，原因是 `app.runtime.model_gateway` 尚不存在。

- [ ] **Step 3: 实现最小模型网关**

实现以下稳定接口：

```python
@dataclass(frozen=True)
class ModelResult:
    content: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

class ModelGateway(Protocol):
    def generate(self, messages: list[dict[str, str]]) -> ModelResult: ...

class OpenAICompatibleModelGateway:
    def generate(self, messages: list[dict[str, str]]) -> ModelResult: ...
```

从同一 ProviderStore 状态解析 `active_model`、Provider 私密配置和模型配置，仅支持 `OpenAIChatModel`；使用同步 `httpx.Client` 调用 `/chat/completions`，设置连接与总超时，并只返回安全异常。

- [ ] **Step 4: 验证测试通过**

Run: `python -m pytest backend/tests/runtime/test_model_gateway.py backend/tests/test_model_providers.py -v`

Expected: PASS。

- [ ] **Step 5: 提交增量**

```powershell
git add backend/app/runtime backend/tests/runtime/test_model_gateway.py
git commit -m "feat: add active model runtime gateway"
```

### Task 2: PlatformAgentHarness 运行闭环

**Files:**
- Create: `backend/app/runtime/harness.py`
- Modify: `backend/app/conversations/repository.py`
- Test: `backend/tests/runtime/test_harness.py`

- [ ] **Step 1: 写成功闭环失败测试**

用内存数据库创建 Conversation、用户 Message 和 queued Run，注入返回“研判完成”的 FakeGateway，执行：

```python
harness.execute(run.id)
assert repository.get_run_by_id(run.id).status == "completed"
assert repository.list_messages_for_run(run.id)[-1].content == "研判完成"
assert [event.event_type for event in repository.list_events(run.id, 0)] == [
    "run.status", "run.status", "message.completed", "run.usage", "run.status"
]
```

- [ ] **Step 2: 运行成功测试并确认失败**

Run: `python -m pytest backend/tests/runtime/test_harness.py::test_completes_run_and_persists_assistant_message -v`

Expected: FAIL，原因是 Harness 尚不存在。

- [ ] **Step 3: 实现成功路径最小代码**

Repository 增加 `get_run_by_id`、`get_run_messages`、`append_event` 和 `add_assistant_message`。Harness 只接受 `actor_type=agent`，按数据库最大序号追加事件，提交 running 和 completed 事务。

- [ ] **Step 4: 验证成功测试通过**

Run: `python -m pytest backend/tests/runtime/test_harness.py::test_completes_run_and_persists_assistant_message -v`

Expected: PASS。

- [ ] **Step 5: 写失败路径测试并确认失败**

FakeGateway 抛出包含伪密钥的异常，断言 Run 为 failed、存在 `run.error` 和最终 `run.status`，且所有 payload 不含伪密钥；另测 `actor_type=team` 返回 `unsupported_actor_type`。

Run: `python -m pytest backend/tests/runtime/test_harness.py -v`

Expected: FAIL，原因是安全失败转换尚未实现。

- [ ] **Step 6: 实现安全失败路径并验证**

Harness 捕获 `ModelRuntimeError` 和兜底异常，回滚当前事务后以安全错误码写入 failed 终态；任何异常文本不直接写入事件。

Run: `python -m pytest backend/tests/runtime/test_harness.py backend/tests/conversations -v`

Expected: PASS。

- [ ] **Step 7: 提交增量**

```powershell
git add backend/app/runtime/harness.py backend/app/conversations/repository.py backend/tests/runtime/test_harness.py
git commit -m "feat: execute agent runs through platform harness"
```

### Task 3: 后台调度与 API 接线

**Files:**
- Modify: `backend/app/conversations/dispatcher.py`
- Modify: `backend/app/conversations/router.py`
- Test: `backend/tests/conversations/test_dispatcher.py`
- Modify: `backend/tests/conversations/test_api.py`

- [ ] **Step 1: 写独立 Session 调度失败测试**

注入测试 SessionFactory 和 RecordingHarnessFactory，调用 `dispatch(run_id)`，等待受控 Future 完成，断言 Harness 收到 Run ID，并断言使用的 Session 与请求 Session 不同。

- [ ] **Step 2: 运行并确认失败**

Run: `python -m pytest backend/tests/conversations/test_dispatcher.py -v`

Expected: FAIL，原因是 `ThreadRunDispatcher` 尚不存在。

- [ ] **Step 3: 实现线程调度器和默认工厂**

`ThreadRunDispatcher` 持有 SessionFactory、Gateway 工厂和有界 `ThreadPoolExecutor`，任务内部创建/关闭 Session。`default_service_factory` 使用应用级 Dispatcher；测试路由仍可注入 `UnavailableRunDispatcher`，保持 API 单元测试确定性。

- [ ] **Step 4: 验证 API 与调度测试**

Run: `python -m pytest backend/tests/conversations/test_dispatcher.py backend/tests/conversations/test_api.py -v`

Expected: PASS，消息创建仍返回 202，SSE 仍遵循 Last-Event-ID。

- [ ] **Step 5: 提交增量**

```powershell
git add backend/app/conversations/dispatcher.py backend/app/conversations/router.py backend/tests/conversations
git commit -m "feat: dispatch agent runs in background"
```

### Task 4: 前端终态轮询和消息刷新

**Files:**
- Modify: `frontend/src/stores/conversations.ts`
- Modify: `frontend/src/stores/conversations.test.ts`

- [ ] **Step 1: 写完成态失败测试**

模拟第一次事件返回 running、第二次返回 completed，断言 `getRunEvents` 使用递增 sequence，完成后调用 `listMessages` 并停止轮询。

- [ ] **Step 2: 写失败态失败测试**

返回 `run.error` 和 failed 状态，断言停止轮询并把安全错误文案写入 store.error。

- [ ] **Step 3: 运行并确认失败**

Run: `npm test -- src/stores/conversations.test.ts`

Expected: FAIL，当前 store 只请求一次事件且不刷新消息。

- [ ] **Step 4: 实现有限轮询**

新增可测试的轮询参数，默认间隔 500ms、最长 120 次；每次按最后 sequence 拉取。`completed` 后加载消息，`failed` 后读取 `run.error` 安全文案；会话切换或新 Run 开始时停止旧轮询。

- [ ] **Step 5: 验证前端测试与构建**

Run: `npm test -- src/stores/conversations.test.ts src/api/runEvents.test.ts`

Run: `npm run build`

Expected: PASS。

- [ ] **Step 6: 提交增量**

```powershell
git add frontend/src/stores/conversations.ts frontend/src/stores/conversations.test.ts
git commit -m "feat: complete chat runs from sse events"
```

### Task 5: 集成验证与文档状态更新

**Files:**
- Modify: `docs/superpowers/plans/2026-08-01-minimal-agent-runtime.md`

- [ ] **Step 1: 运行后端全量测试**

Run: `python -m pytest backend/tests -v`

Expected: PASS。

- [ ] **Step 2: 运行前端全量测试和构建**

Run: `npm test`

Run: `npm run build`

Expected: PASS。

- [ ] **Step 3: 检查差异和敏感信息**

Run: `git diff --check`

Run: `git grep -n "runtime-secret" -- ':!backend/tests/**'`

Expected: 无格式错误，生产文件无测试密钥。

- [ ] **Step 4: 更新计划勾选状态并提交**

```powershell
git add docs/superpowers/plans/2026-08-01-minimal-agent-runtime.md
git commit -m "docs: complete minimal agent runtime plan"
```
