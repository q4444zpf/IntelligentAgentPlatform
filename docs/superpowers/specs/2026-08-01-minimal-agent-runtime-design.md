# 最小智能体运行闭环设计

## 目标

让 Web 问答从“仅创建 queued Run”升级为可调用平台当前启用的默认模型、持久化助手回复，并通过现有 SSE 接口反馈运行状态的最小生产闭环。

## 范围

本阶段支持单智能体文本问答，不引入 LangGraph、Deep Agents、RAG、工具调用、多智能体协同或 stdio MCP Worker。现有 `actor_type=team` 请求明确返回失败事件，避免伪装成已支持的多智能体运行。

## 方案选择

采用进程内后台调度器和可替换模型网关：消息接口仍返回 HTTP 202，提交事务后由后台线程使用独立数据库会话处理 Run。该方案保持当前 API 和 SSE 契约，部署依赖最少；未来接入任务队列时，只替换 `RunDispatcher`，不改变 Harness、模型网关或事件结构。

未采用同步阻塞调用，因为会破坏当前异步 Run 契约并增加请求超时风险。暂不采用 Celery/Redis，因为当前单体部署阶段无需引入额外基础设施。

## 组件

- `PlatformAgentHarness`：加载 Run、对话历史和默认模型，驱动状态迁移并持久化结果。
- `ModelGateway`：定义文本生成接口；第一版实现 OpenAI-compatible `/chat/completions`，从服务端 ProviderStore 解析默认模型、API Key、Base URL、请求参数和自定义请求头。
- `ThreadRunDispatcher`：接收 Run ID，在独立线程和独立 SQLAlchemy Session 中调用 Harness；API 请求线程不复用数据库会话。
- `ConversationRepository`：补充按 Run ID 加载执行上下文、追加事件和助手消息的方法。

## 数据流

1. 用户消息、AgentRun 和 `run.status=queued` 在同一事务提交。
2. Dispatcher 获得 Run ID，并在独立会话中启动 Harness。
3. Harness 将 Run 更新为 `running`，写入递增序号的 `run.status` 事件。
4. Harness 读取对话消息和平台当前启用的默认模型。
5. ModelGateway 调用模型并返回标准化文本及用量信息。
6. Harness 在同一事务中写入 assistant Message、`message.completed` 事件和 `run.status=completed`。
7. 前端按 `Last-Event-ID` 轮询 SSE；完成后重新加载消息列表。

## 默认模型与凭据

运行时每次执行 Run 都读取 `ProviderService.get_active()`，不在 Agent 代码中固定 DeepSeek。默认模型为空、Provider 未启用、模型不可用或密钥缺失时，Run 转为 `failed`。API Key 只在模型网关内读取并放入上游请求头，不进入 RunEvent、日志、消息或异常详情。

## 状态与事件

允许的最小状态流为 `queued -> running -> completed|failed`。事件顺序由数据库生成：

- `run.status {status: queued}`
- `run.status {status: running}`
- `message.completed {message_id, role: assistant}`
- `run.usage {prompt_tokens, completion_tokens, total_tokens}`，仅在上游返回用量时写入
- `run.status {status: completed}`

失败时写入 `run.error {code, message}` 和 `run.status {status: failed}`。错误消息使用平台定义的安全文案，不包含密钥、完整上游响应或请求头。

## 错误处理

默认模型未配置、协议暂不支持、上游连接错误、HTTP 非成功状态、响应结构错误均转为可审计失败事件。后台任务必须捕获未处理异常并保证 Run 不停留在 `running`。首版设置明确的连接和总请求超时，不自动重试非幂等模型调用。

## 前端行为

沿用当前页面样式，只调整数据行为。发送后显示运行中状态，持续读取事件；收到 `completed` 或 `failed` 后停止轮询。完成时重新加载消息，失败时显示安全错误文案。刷新页面后仍可通过持久化消息和 Run 事件恢复结果。

## 测试与验收

- Harness 使用假 ModelGateway 验证成功状态、助手消息、事件顺序和用量记录。
- 验证默认模型缺失和上游错误时 Run 进入 failed 且凭据不泄漏。
- 验证 Dispatcher 使用独立 Session 并能完成后台执行。
- API 测试验证 202 响应和 SSE 增量读取不回归。
- 前端测试验证完成后刷新消息、失败后停止轮询并展示错误。
- 后端与前端相关测试、静态检查和构建全部通过。

## 后续演进

第二阶段在 Harness 内接入配置解析器；第三阶段接入 LangGraph 和 Deep Agents；第四阶段将进程内 Dispatcher 替换为队列 Worker，并接入 Tool Gateway、Sandbox Manager 和 stdio MCP Worker。
