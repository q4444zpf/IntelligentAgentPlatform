# MCP 工具注册与 Agent 绑定设计

## 1. 目标与范围

本阶段将 MCP `tools/list` 发现的工具注册到统一工具目录，并允许管理员审核发布后由 Agent 绑定。

本阶段包含：

- Streamable HTTP 和 SSE MCP 工具同步到统一工具目录；
- MCP 工具来源映射、生命周期同步和默认未发布策略；
- 工具管理员发布、取消发布、启用和停用；
- Agent 仅绑定已发布、已启用且当前可用的 MCP 工具；
- PostgreSQL 迁移、备份和回归测试。

本阶段不包含：

- MCP `tools/call` 实际执行；
- stdio MCP Worker；
- MCP 凭据迁移到 Secret Store；
- 独立 `mcp_tools` 关系表和多租户资源域重构。

## 2. 架构边界

MCP 管理模块仍负责客户端配置、认证信息、`tools/list` 和客户端级白名单。统一工具目录负责工具审核、发布状态、Agent 可绑定性和后续 Tool Gateway 路由所需的来源标识。

Agent 配置只保存统一工具 ID，不保存 MCP URL、Header、命令、环境变量或原始客户端配置。Agent 运行时仍只从统一工具目录解析工具定义，不能绕过 Tool Gateway 直接连接 MCP Server。

数据流如下：

```text
MCP tools/list
  -> 校验远程工具名称和 inputSchema
  -> 更新 mcp_clients.tool_records
  -> 生成稳定统一工具 ID
  -> 写入 registered_tools，默认未发布
  -> 管理员审核并发布
  -> Agent 管理页面选择工具 ID
  -> Agent 保存时再次校验发布、启用和可用状态
```

## 3. 数据模型

扩展 `registered_tools`：

- `source_resource_id VARCHAR(128) NULL`：来源资源标识。MCP 工具保存客户端 `client_key`。
- `source_capability_id VARCHAR(256) NULL`：来源能力标识。MCP 工具保存远程工具原始名称。
- `source_available BOOLEAN NOT NULL DEFAULT TRUE`：来源当前是否可用，与管理员控制的 `enabled` 分离。

内置工具的两个字段均为 `NULL`。MCP 工具必须同时填写两个字段。

统一工具 ID 使用以下规则：

```text
mcp.<client_slug>.<tool_slug>_<hash8>
```

- slug 仅包含小写字母、数字和下划线，满足现有工具 ID 校验规则；
- `hash8` 由 `client_key + NUL + remote_tool_name` 的 SHA-256 前 8 位生成；
- 工具 ID 最长 128 字符，slug 超长时截断但保留哈希；
- 相同客户端与远程工具名称始终生成相同 ID；
- 真实反向映射使用来源字段，不解析工具 ID。

数据库表结构修改前，必须执行带时间戳的本地 `pg_dump`。备份文件写入已被 Git 忽略的本地备份目录，不提交密码或备份内容。迁移必须同时提供 downgrade。

## 4. 同步与生命周期状态机

首次发现工具时写入统一工具目录：

- `source=mcp`
- `published=false`
- `enabled=true`
- `source_available=true`
- `risk_level=medium`
- `requires_approval=false`
- `input_schema` 使用 MCP `inputSchema`
- `output_schema` 使用宽松对象 Schema，实际调用阶段再完善结果契约

再次同步已存在工具时：

- 更新名称、描述、输入 Schema 和来源标识；
- 不覆盖管理员设置的风险等级和审批要求；
- 不把未发布工具自动发布；
- 正常存在、客户端启用且在白名单内时设置 `source_available=true`，不覆盖管理员设置的 `enabled`；
- 曾因消失、客户端停用或退出白名单而不可用的工具重新出现时，保持 `published=false`，要求重新审核。

客户端停用、工具退出白名单或最新同步结果中消失时：

- 对应统一工具记录设置 `source_available=false`；
- 同时设置 `published=false`；
- 不删除工具记录；
- 不删除 Agent 历史绑定和运行记录；
- Agent 编辑页面显示为不可用，保存时必须移除该绑定。

删除 MCP 客户端时，同样停用并取消发布其全部工具，再删除客户端记录。两个操作必须处于同一数据库事务；任一失败则整体回滚。

## 5. 工具审核与发布

工具注册中心增加显式发布操作，发布与启停分离：

- `发布`：只允许单位管理员；要求 MCP 客户端启用、工具仍存在且在客户端白名单内。
- `取消发布`：只允许单位管理员；立即使新 Agent 无法绑定，已有 Agent 绑定保留但不可执行。
- `启用/停用`：只修改管理员治理字段 `enabled`；MCP 来源工具即使被管理员启用，在 `source_available=false` 时仍不可绑定。

公开工具接口不得返回 MCP Header、env、URL 中的凭据或其他客户端密钥。来源字段只返回客户端标识和远程工具名称。

## 6. Agent 绑定规则

Agent 创建、更新、复制和设置默认 Agent 时，后端通过 `ToolService.resolve_bindable()` 校验所有 `tool_ids`：

- 工具必须存在；
- `published=true`；
- `enabled=true`；
- `source_available=true`；
- MCP 来源工具对应客户端必须存在且启用；
- MCP 工具必须仍在客户端工具列表和白名单中。

Agent 管理页面继续读取统一工具列表。未发布工具不进入可选列表；已绑定但失效的工具显示为不可用，并阻止保存，直到管理员移除绑定。

## 7. 一致性与错误处理

MCP 同步、白名单更新、客户端启停和客户端删除涉及 MCP 与工具目录两个模块，必须共享同一 SQLAlchemy Session 和事务。

- `tools/list` 网络失败：不修改 MCP 工具快照和统一工具目录。
- Schema 非法：整次同步失败，不进行部分注册。
- 工具目录写入失败：回滚 MCP 快照和管理审计。
- 并发修改：沿用 MCP 版本 CAS，冲突返回 409。
- 发布失效 MCP 工具：返回 422，并记录失败管理审计。

## 8. 审计

保留现有 MCP 同步管理事件，并为工具发布、取消发布和来源导致的停用记录工具管理事件。审计元数据只允许包含工具数量、状态和来源标识，不包含 MCP Header、env 或完整请求/响应。

本阶段不生成 `tool.invoke.*` 运行审计，因为尚未执行 MCP `tools/call`。

## 9. 测试与验收

后端 TDD 覆盖：

- 首次同步注册工具且默认未发布；
- 特殊字符、中文和超长名称生成稳定合法工具 ID；
- 重复同步不覆盖管理员治理字段；
- 发布后允许 Agent 绑定，未发布时拒绝；
- 客户端停用、白名单变化、工具消失和客户端删除会标记来源不可用并取消发布；
- 工具重新出现不会自动重新发布；
- MCP 同步与工具目录写入原子回滚；
- PostgreSQL upgrade/downgrade 和数据保留；
- API 响应与审计不泄露凭据。

前端测试覆盖：

- 工具注册中心显示 MCP 来源和发布状态；
- 单位管理员可以发布和取消发布；
- Agent 页面只提供可绑定工具；
- 已绑定失效工具有明确提示并阻止保存。

验收完成后，数据库中不保留临时 MCP 客户端、临时工具或测试 Agent。stdio MCP 继续显示为等待沙箱 Worker，不能在 API 进程中启动。
