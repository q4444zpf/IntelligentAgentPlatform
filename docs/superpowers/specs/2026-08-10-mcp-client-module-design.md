# MCP 客户端模块完善设计

## 1. 目标与范围

本阶段完善 MCP 客户端管理模块，首期完整支持 Streamable HTTP 和 SSE，保留 stdio 配置登记但显示“等待沙箱 Worker”。目标是让单位管理员可以安全登记、测试、同步、监控、授权、停用和归档 MCP 服务，并让 Agent 只能使用当前单位/项目范围内已发布、已启用且来源在线的工具。

本阶段不在 FastAPI 进程中启动不可信 stdio 命令；stdio 执行留给后续沙箱 Worker。

## 2. 关键策略

- 新增 MCP 保存后自动启动测试与工具同步任务。
- 测试失败仍保留配置，状态显示失败阶段和脱敏错误，允许重试。
- 启用的 HTTP/SSE MCP 每 5 分钟健康检测一次，并支持手动检测。
- 远端新增工具默认未发布；Schema/描述变化标记待复核并取消发布；删除工具标记来源不可用并取消发布。
- MCP 客户端按单位归属，按项目授权；单位管理员管理连接和发布，项目管理员只能查看和使用授权能力。
- MCP Header/API Key 只保存凭据引用，密钥由后端临时解析注入，不进入响应、日志、审计或 Agent 配置。
- 删除采用归档：停止检测和调用，保留配置快照、历史绑定、运行记录和审计记录。
- 来源离线时保留工具历史记录，但 `source_available=false`，禁止绑定、保存和执行。

## 3. 数据模型

### 3.1 mcp_clients

保存稳定内部 ID、单位 ID、单位内唯一客户端标识、显示信息、传输方式、服务地址、凭据引用、启停/归档状态、协议版本、健康状态、最近检测/成功时间、耗时、连续失败次数、脱敏错误和健康任务租约。

### 3.2 mcp_project_grants

保存 MCP 客户端与项目的授权关系。查询、绑定和执行均校验当前单位和项目授权。

### 3.3 mcp_tools

保存远端工具名、描述、输入 Schema、Schema 哈希、版本、首次/最后发现时间、来源可用性和待复核状态，并与统一 `registered_tools` 建立稳定关联。

### 3.4 credentials

凭据按单位归属独立加密保存。MCP 配置只保存 `credential_id`，页面不能回显密钥；凭据失效或删除时相关 MCP 进入认证失败并取消工具发布。

### 3.5 mcp_health_checks 与 mcp_operations

分别保存检测历史摘要和测试/同步任务状态。健康任务使用数据库租约，避免多实例重复执行。数据库表结构每次变更前生成本地 PostgreSQL 备份。

## 4. 后端模块边界

```text
mcp/
  config_service.py       配置、权限、归档
  protocol.py             initialize/initialized/tools/list
  transports/
    streamable_http.py
    sse.py
  discovery_service.py    工具发现、差异计算和同步
  health_service.py       手动与定时检测
  credential_resolver.py  临时凭据注入
  scheduler.py            五分钟健康任务和租约
  router.py               API 边界
```

统一工具注册中心继续负责发布审核和 Agent 可执行性，MCP 模块不重复实现这些规则。

## 5. 协议流程

### 5.1 Streamable HTTP

发送 `initialize`，兼容 JSON 与 `text/event-stream`，保存临时 `Mcp-Session-Id`，发送 `notifications/initialized`，分页调用 `tools/list` 直到完成，校验 Schema，测试结束按协议关闭临时会话。连接、读取和总超时分别限制。

### 5.2 SSE

建立 SSE 事件流，读取消息提交地址，完成 `initialize` 和 `notifications/initialized`，通过 JSON-RPC 请求 ID 关联响应，分页调用 `tools/list`，完成后关闭事件流。

网络请求不持有数据库事务；工具差异在事务内更新客户端工具、统一目录和发布状态。

## 6. 工具差异

- `added`：新增工具进入目录，默认未发布。
- `changed`：描述或 Schema 变化，版本递增、标记待复核并取消发布。
- `removed`：标记来源不可用并取消发布，保留历史绑定。
- `unchanged`：保留当前审核和发布状态。

## 7. API 与前端

核心接口：

```text
POST /api/mcp/test
POST /api/mcp
GET  /api/mcp/{client_id}/operations/{operation_id}
POST /api/mcp/{client_id}/connection-test
POST /api/mcp/{client_id}/sync
GET  /api/mcp/{client_id}/health
GET/PUT /api/mcp/{client_id}/projects
POST /api/mcp/{client_id}/archive
POST /api/mcp/{client_id}/restore
```

编辑弹窗提供“测试连接、保存、取消”。测试展示握手阶段、耗时、协议版本、工具数量和脱敏错误；列表展示在线/异常/离线/认证失败/未检测/等待沙箱、最近检测时间、工具数量、快捷测试和同步；工具抽屉展示同步时间、差异、Schema 版本、来源可用性和发布状态。

## 8. 健康与权限

状态为“未检测、在线、异常、离线、认证失败、已停用、已归档、等待沙箱”。连续失败退避；恢复在线不自动重新发布。所有后端接口重新校验单位、项目、角色、客户端状态、工具发布、启用和来源可用性。

## 9. 验收标准

1. 单位管理员新增 DeepWiki Streamable HTTP 客户端，测试看到握手成功和工具数量。
2. 保存后后台任务完成，列出全部远端工具。
3. 新增工具默认未发布；Schema 变化显示待复核；删除工具显示来源不可用。
4. 停用或断开服务显示离线，Agent 不能保存或执行来源工具。
5. 恢复后状态在线但不自动发布，管理员发布后授权项目 Agent 才能绑定调用。
6. 项目管理员不能修改连接、凭据或发布工具。
7. 归档隐藏客户端但保留审计、绑定和运行记录。
8. 会话在用户持续操作时自动续期，空闲超时仍跳转登录并保留原目标。
9. 后端、前端测试和构建全部通过，日志中不出现凭据明文。
