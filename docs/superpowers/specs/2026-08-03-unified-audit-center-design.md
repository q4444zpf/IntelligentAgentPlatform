# 统一审计中心设计

## 1. 背景与目标

平台已在 PostgreSQL 中持久化 Agent Run、RunEvent 和 ToolInvocation，并由 `/runs` 提供 Agent 专项审计。当前 `/system/audit` 仍是占位页面，MCP、知识库、沙箱、LLM 和管理操作也没有统一审计契约。

本设计面向单单位、多项目、多角色场景，统一覆盖运行行为与管理操作，提供跨模块检索、关联追踪和专业详情跳转，并为后续 MCP、知识库、沙箱、LangGraph 和 Deep Agents 接入保留稳定边界。

## 2. 已确认决策

- 运行审计和管理操作审计使用同一中心。
- 单位审计员查看本单位，项目管理员查看本项目，普通用户仅查看本人。
- 默认只保存脱敏摘要和结构化元数据；原文留在业务表或受控对象存储。
- 使用 PostgreSQL 独立追加式 `audit_events` 表。
- 保持统一部署，不引入消息队列或独立审计微服务。
- `/runs` 保持 Agent 专项视图，统一审计不复制完整 Timeline。

## 3. 第一版范围

包含：

- Agent Run 创建、完成、失败和取消。
- 内置工具调用开始、完成和失败。
- LLM 调用成功、失败、Token 用量和耗时。
- 智能体、工具、MCP 客户端和模型配置的创建、修改、启停、发布、授权、删除及失败尝试。
- 分页、筛选、汇总、详情、Trace 时间线、分级权限和安全 404。
- 用真实页面替换 `/system/audit` 占位页。

不包含：

- 伪造尚未实现的知识库或沙箱运行记录。
- 原始提示词、完整响应、API Key、认证头、环境变量或文件内容入审计表。
- 审计记录在线修改、业务 API 删除或同步大批量导出。
- 消息队列、独立审计服务、归档服务或外部 SIEM。
- 重复实现 `/runs` 的 Agent Timeline 和工具详情。

## 4. 架构与边界

- `AuditRecorder`：验证范围、执行脱敏、生成幂等键并追加事件。
- `AuditRepository`：范围内列表、汇总、详情和 Trace 查询。
- `AuditPolicy`：根据单位、项目、角色和用户生成强制查询条件。
- `AuditRedactor`：递归屏蔽密钥字段、认证信息和超长内容。
- `AuditService`：组合策略和 Repository，向 FastAPI 提供稳定契约。
- `AuditView`：展示统一列表与关联时间线，并跳转专业详情。

业务模块只依赖 Recorder 接口，不直接写表。未来可把 Recorder 替换为事务 Outbox 和异步消费者，不改变业务调用方、API 或前端。

现有 `agent_runs`、`run_events` 和 `tool_invocations` 继续作为 Agent 专业记录。统一审计只记录关键节点、脱敏摘要和 `run_id`。历史 Run 迁移时每个 Run 只生成一条 `agent.run_snapshot`，并标记 `backfilled=true`，不伪造历史逐步事件。

## 5. 数据模型

新增 `audit_events`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | String(36) | 主键 |
| `unit_id` | String(64) | 单位范围，必填 |
| `project_id` | String(64), nullable | 单位级操作可为空 |
| `user_id` | String(64), nullable | 系统任务可为空 |
| `actor_role` | String(40), nullable | 事件发生时角色快照 |
| `category` | String(30) | `runtime` 或 `management` |
| `source` | String(30) | `agent/tool/mcp/knowledge/sandbox/llm/system` |
| `action` | String(100) | 稳定动作名 |
| `status` | String(30) | `started/succeeded/failed/cancelled` |
| `risk_level` | String(20) | `low/medium/high/critical` |
| `trace_id` | String(64), nullable | 跨模块关联 |
| `run_id` | String(36), nullable | Agent Run 关联 |
| `parent_event_id` | String(36), nullable | 父事件，不级联删除 |
| `resource_type` | String(50), nullable | 对象类型 |
| `resource_id` | String(128), nullable | 对象标识 |
| `resource_name` | String(200), nullable | 发生时名称快照 |
| `summary` | Text | 长度受限的脱敏摘要 |
| `metadata_json` | JSON | 动作 Schema 白名单元数据 |
| `error_code` | String(120), nullable | 稳定错误码 |
| `duration_ms` | Integer, nullable | 执行耗时 |
| `idempotency_key` | String(180) | 唯一幂等键 |
| `occurred_at` | DateTime(timezone=True) | 业务发生时间 |
| `created_at` | DateTime(timezone=True) | 写入时间 |

索引覆盖单位时间、单位项目时间、单位项目用户时间、Trace、Run、来源动作状态；`idempotency_key` 唯一。应用层不提供更新和删除方法。生产应用账号只授予 `SELECT/INSERT`，迁移账号保留 DDL 权限。首版不自动清理记录，归档与保留期根据真实容量指标另行设计。

## 6. 身份与权限

现有 RequestContext 只有 `user_id/project_id/user|admin`，需要扩展 `unit_id` 和角色集合，至少支持：

- `unit_auditor`：强制当前单位，可选项目和用户。
- `project_admin`：强制当前单位和项目，可选用户。
- `user`：强制当前单位、项目和本人。

生产身份来自认证后的服务端 Claims，不信任浏览器自由提交角色。列表、汇总、详情和相关事件均使用同一个 AuditPolicy。多角色取已授权的最大范围，但绝不合并其他单位或项目。越权详情统一返回 `404 记录不存在或无权访问`。

## 7. 写入、一致性与幂等

### 7.1 管理操作

业务变更和审计记录使用同一 SQLAlchemy Session：

1. 校验权限和输入。
2. 执行业务变更。
3. 生成允许字段的变更摘要。
4. 追加管理事件。
5. 一次提交。

审计写入失败时整个变更回滚。权限拒绝、输入校验失败和资源不存在等未产生业务变更的尝试，写入独立 `failed` 事件，只记录动作、范围、稳定错误码和脱敏对象摘要，不保存被拒绝的敏感输入。

### 7.2 外部运行

外部调用开始前写 `started`；成功、失败或取消后写对应结束事件，通过 Trace 和父事件关联。外部操作已完成而结束事件写入失败时不回滚外部结果，服务记录结构化错误和指标，并使用相同幂等键有限重试，不向用户伪报已写入审计。

幂等键示例：

- `agent:{run_id}:status:{status}`
- `tool:{invocation_id}:{status}`
- `llm:{run_id}:{iteration}:{status}`
- `management:{request_id}:{action}:{resource_id}`

唯一冲突返回已有事件，不创建重复记录。

## 8. 脱敏规则

AuditRedactor 递归处理字典和数组：

- `api_key/authorization/token/password/secret/cookie/env` 替换为 `[REDACTED]`。
- 字符串限制长度，截断时写 `truncated=true`。
- metadata 只接受动作 Schema 声明字段，未知字段丢弃。
- 文件只记录 Artifact ID、文件名、安全类型和大小，不记录绝对路径或内容。
- Shell 只记录命令类型、退出码、沙箱 ID 和脱敏摘要，不记录环境变量。
- 模型只记录 provider/model、Token、耗时和错误码，不记录密钥或完整提示词。
- 前端按纯文本或只读 JSON 渲染，不执行 HTML。

## 9. API

### 9.1 列表

`GET /api/audit/events`

参数：`page`、`page_size`、`category`、`source`、`action`、`status`、`risk_level`、权限范围内的 `project_id/user_id`、`query`、`occurred_after/occurred_before`。

时间必须带时区且开始不晚于结束。Query 只匹配事件 ID、Trace ID、Run ID、资源 ID 或名称，通配符按字面量处理。排序固定为 `occurred_at DESC, id DESC`。响应包含分页项、总数和完整筛选范围的总数、失败、高风险、运行、管理及来源统计。

### 9.2 详情与关联

- `GET /api/audit/events/{event_id}`：脱敏详情和专业详情链接。
- `GET /api/audit/events/{event_id}/related`：先校验锚点，再按同一 Trace 和当前权限范围查询，按时间正序。

非法参数返回 422，未认证返回 401，缺少页面权限返回 403，具体记录越权返回安全 404。第一版不提供同步导出；未来导出必须使用受控异步任务、有效期和二次权限检查。

## 10. 页面

`/system/audit` 替换 GenericModuleView，沿用平台布局与 Ant Design Vue：

- 顶部：总事件、失败、高风险、运行事件、管理操作。
- 筛选：时间、项目、用户、类别、来源、状态、风险和关键词。
- 表格：时间、类别、来源、动作、操作人、项目、对象、结果、风险、耗时。
- 详情抽屉：基础信息、脱敏摘要、metadata 和关联 Timeline。
- Agent 事件提供“查看 Agent Run”，跳转 `/runs` 对应 Run。
- 未实现来源只保留稳定枚举，不显示虚假数据。

列表、详情和 Timeline 使用独立 AbortController 与请求代次，支持懒加载、缓存和独立重试。未知枚举使用中性标签；已删除对象保留事件快照并禁用专业详情入口。

## 11. 首期事件映射

| 业务节点 | 动作 | 类别/来源 |
| --- | --- | --- |
| Agent Run 创建/完成/失败/取消 | `agent.run.created/completed/failed/cancelled` | runtime/agent |
| 工具开始/成功/失败 | `tool.invoke.started/succeeded/failed` | runtime/tool |
| LLM 成功/失败 | `llm.invoke.succeeded/failed` | runtime/llm |
| 配置创建/修改/启停 | `resource.created/updated/enabled/disabled` | management/对应资源来源 |
| 发布/授权/删除 | `resource.published/permission_changed/deleted` | management/对应资源来源 |
| 操作被拒绝或校验失败 | 原动作名，状态为 `failed` | management/对应资源来源 |

“对应资源来源”是对象所属稳定枚举：智能体为 `agent`，工具为 `tool`，MCP 客户端为 `mcp`，模型配置为 `llm`。知识库、真实 MCP 执行和沙箱实现后复用既有 source 与 Recorder，不修改表或页面主契约。

## 12. 测试与验收

后端覆盖：

- 三种角色及跨单位、项目、用户隔离。
- 列表、汇总、详情和 Trace 权限一致。
- 管理操作与审计同事务成功或回滚。
- 外部结束事件幂等重试。
- 密钥、认证、环境变量、路径和超长内容脱敏。
- 稳定分页、字面量搜索、时区日期和枚举校验。
- 历史 Run snapshot 可重复回填。
- PostgreSQL 迁移、索引和唯一约束集成测试。

前端覆盖：

- 列表、汇总、筛选和分页。
- 角色控制项目与用户筛选器。
- 详情与 Timeline 懒加载、取消、缓存和独立重试。
- 安全 404、未知枚举、删除对象和空 Timeline。
- Agent Run 跳转及敏感内容不渲染。

浏览器验收：

1. 修改测试智能体后出现 management 事件。
2. 执行一次包含两个内置工具的 Agent Run。
3. 出现关联的 Agent、Tool 和 LLM 事件，Trace 顺序正确。
4. Agent 事件能跳转 `/runs` 详情。
5. 三种角色的数据范围符合权限规则。
6. 桌面和移动视口无重叠、无控制台错误。

## 13. 分阶段实施

1. 表、迁移、脱敏、Repository、权限和 API。
2. Agent、工具、LLM 接入及历史 snapshot 回填。
3. 智能体、工具、MCP、模型配置管理操作接入。
4. `/system/audit` 页面、详情和专业跳转。
5. PostgreSQL、权限和浏览器全链路验收。
6. 知识库、真实 MCP 和沙箱实现时分别接入 Recorder。

## 14. 完成标准

- `/system/audit` 不再是占位页。
- 运行和管理操作产生真实、不可修改、可关联的脱敏事件。
- 分级权限在列表、汇总、详情和 Timeline 一致生效。
- `/runs` 保持可用并支持从统一审计跳转。
- 新来源通过 Recorder 接入，无需修改统一表和页面主契约。
- 自动化测试、PostgreSQL 集成测试和真实浏览器验收通过。
