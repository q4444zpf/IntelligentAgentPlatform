# 首批原型界面冻结设计

## 1. 冻结目标

本设计冻结生产项目第一阶段的两个核心页面：AI 问答工作台和智能体管理/设置页。冻结的是信息架构、核心交互、状态语义和前后端契约，不冻结最终视觉细节，也不要求在本阶段实现 Deep Agents、LangGraph、工具网关、知识检索、沙箱执行器或 GIS Renderer。

首批页面必须满足以下原则：

- 现有三栏工作台和平台管理框架继续使用，不进行整体重做；
- 页面只能展示后端已经确认的运行事实，不模拟智能体回复、协同过程或沙箱状态；
- 单智能体和多智能体共享 Conversation、Message、AgentRun、RunEvent 和 Artifact 契约；
- 页面只提交业务意图和结构化上下文，不调用 Agent 内部框架，也不向 Agent 暴露 Vue、OpenLayers 或浏览器方法；
- 未完成的执行能力显示明确的不可用或等待状态，不能用演示数据伪装为生产能力；
- 第一期不提供真实设备控制，任何控制类输出只能形成建议、待审批事项或模拟方案。

## 2. 审查结论

### 2.1 AI 问答页现状

现有页面已经具备适合水利生产人员的工作台结构：左侧会话，中间对话与执行主体，右侧运行上下文、协同成员、知识库、业务资源和执行策略。单智能体/多智能体切换、知识库选择、资源选择、协同步骤、引用以及报告/GIS入口均具有保留价值。

当前不能冻结为生产行为的内容包括：

- 会话和消息是固定数组并保存在 `sessionStorage`；
- 发送消息后等待 800 ms 生成固定回复；
- 页面无条件显示“沙箱已隔离”；
- 协同成员在线、执行完成、耗时、引用、报告和 GIS 结果均为演示数据；
- “查看调度建议书”和“进入 GIS 专业工作区”没有 Artifact 契约支撑；
- 消息输入、附件、取消运行、重试、审批、断线续传和错误恢复没有真实状态模型。

因此保留页面结构，替换状态来源和交互语义。

### 2.2 智能体设置页现状

现有页面已经实现智能体列表、创建/编辑弹窗、Web/Desktop/Common 运行形态、模型选择、Skill 绑定、系统提示词、上下文提示词和人工确认策略。这些字段可作为第一期配置基础。

当前缺少的生产配置边界包括：

- Agent 版本、草稿/发布状态和版本快照；
- Deep Agents 运行模式及可用能力说明；
- Tool Registry 工具授权，现有 Skill 绑定不能代替工具授权；
- MCP 来源只应通过已发布 Tool 间接选择；
- 默认知识库和业务资源绑定；
- 文件、代码、Shell、网络和子智能体能力开关；
- Workflow Runner 与 Action Sandbox 策略的只读摘要；
- Token、运行时长、并发和文件空间限制；
- 配置有效性检查和发布前验证结果。

当前接口返回 HTTP 500 时页面能显示错误，但缺少重试说明和创建按钮的可用性边界。接口不可用时不得允许用户填写后在提交阶段才发现整个服务不可用。

## 3. 方案选择

采用“最小范围冻结、契约驱动替换”的方案：

- 保留现有视觉布局、导航结构和大部分组件；
- 优先接通持久化会话、消息、Run 和 SSE 事件；
- 将协同步骤、引用、审批和成果统一映射到 RunEvent/Artifact；
- 扩充智能体配置的信息结构，但只启用后端已支持的字段；
- LangGraph 画布、工具注册中心、沙箱监控和 GIS 专业工作区继续作为独立页面演进。

不采用“只接接口不改原型语义”，因为这会保留虚假状态；也不采用整体重做，因为当前布局已经覆盖主要业务工作流，重做不能提高第一阶段闭环速度。

## 4. AI 问答工作台冻结设计

### 4.1 页面区域

桌面端继续使用三栏布局：

1. 会话栏：新建、搜索、最近会话、归档入口；
2. 主工作区：模式与执行主体、上下文选择、消息流、运行轨迹、成果和输入区；
3. 上下文栏：当前项目、数据时刻、团队成员、知识库、业务资源和执行策略摘要。

移动端按“会话 / 对话 / 上下文”三个视图切换，不同时渲染三列。主消息输入区保持稳定高度，运行事件和 Artifact 加载不得导致输入区跳动。

### 4.2 会话与模式

- 会话只从服务端加载，所属范围为当前项目和当前用户；
- 新建会话可以先显示空工作区，首次发送时创建 Conversation；
- 切换会话时加载历史 Message，并清空上一个会话的活动 Run 和临时页面状态；
- 单智能体提交 `actor_type=agent` 和 `actor_id`；
- 多智能体提交 `actor_type=team` 和 `actor_id`；
- 团队必须是平台已发布的团队版本，后续由 LangGraph 执行；前端不自行拼装成员调用顺序；
- 模式、执行主体、知识库和业务资源是会话输入上下文，不是浏览器本地权威数据。

一期私有会话不允许同项目其他用户直接读取。团队共享会话以后通过显式成员和 ACL 设计实现，不通过放宽项目查询模拟共享。

### 4.3 消息发送与运行状态

发送消息的固定链路为：

1. 前端通过 REST 提交 Message、执行主体和允许的结构化上下文；
2. API 在同一事务中创建 Message、AgentRun 和初始 RunEvent；
3. 前端立即显示用户消息和 `queued` 状态；
4. 前端通过 SSE 读取持久化 RunEvent，并记录最后 `sequence`；
5. 断线后使用 `Last-Event-ID` 续传；
6. 最终状态以 PostgreSQL 中的 AgentRun 为准。

首批状态文案固定为：

| Run 状态 | 页面文案 | 行为 |
| --- | --- | --- |
| 无 Run | 尚未启动运行 | 允许发送 |
| queued | 等待沙箱执行服务 | 允许取消，不显示“正在分析” |
| starting | 正在创建隔离运行环境 | 禁止重复发送当前任务 |
| running | 沙箱运行中 | 显示真实步骤和增量内容 |
| waiting_approval | 等待人工确认 | 展示审批操作 |
| succeeded | 运行完成 | 展示持久化结果和成果 |
| failed | 运行失败 | 展示可追踪错误和重试入口 |
| cancelled | 已取消 | 保留已产生的事件和成果 |

只有 Workflow Runner 和必要的 Action Sandbox 已成功创建并通过服务端事件确认后，页面才可显示“沙箱已隔离”或“沙箱运行中”。Sandbox Executor 不可用时保持 `queued` 或进入明确失败状态，禁止回退到 API/Worker/宿主机执行。

### 4.4 RunEvent 展示

主工作区按事件类型渲染运行事实：

- `run.status`：更新运行状态；
- `message.delta`：合并流式回答；
- `message.completed`：登记持久化助手消息；
- `step.started`、`step.progress`、`step.completed`、`step.failed`：更新单智能体或团队步骤；
- `approval.requested`、`approval.resolved`：展示和关闭人工确认区；
- `citation.created`：展示引用；
- `artifact.created`、`artifact.updated`：登记或更新成果；
- `run.error`：展示面向用户的错误摘要和 `trace_id`。

客户端按 `run_id + sequence` 去重和排序。未知事件类型写入诊断日志但不阻断对话；未知 Schema 版本显示兼容性提示。

### 4.5 Artifact 与 GIS

报告、文件、图表、表格和 GIS 结果都以 Artifact 展示，不把可执行组件定义写入消息正文。

- 消息中的成果按钮由 Artifact 元数据生成；
- `report.document`、`file.download` 可在首批会话链路完成后逐步接入；
- `chart.timeseries`、`table.data` 使用独立 Renderer；
- `gis.map.2d` 和 `gis.profile` 进入 GIS 专业工作区或对话内预览；
- OpenLayers Renderer 通过动态 `import()` 懒加载；
- 大型 GIS 数据不进入 SSE，只传 Artifact 元数据和受权资源地址；
- 用户地图操作通过 REST 提交 Artifact Action 或 `ui.context`，Agent 不调用前端组件方法。

Artifact Renderer 加载失败不能中断消息和 SSE 消费。页面显示“预览不可用”，保留重新加载和安全下载入口。

### 4.6 输入、审批和错误状态

- 消息为空、没有执行主体或当前项目不可用时禁止发送；
- 附件入口在文件上传和 Artifact 输入契约完成前显示禁用状态，不保留无反馈按钮；
- `@成员` 仅用于已发布团队中的成员提示，不改变 LangGraph 图；
- 运行期间允许取消，取消请求通过 REST 提交；
- 审批操作必须展示工具、参数摘要、风险等级、影响范围和超时时间；
- 页面刷新后通过 Conversation、AgentRun 和 RunEvent 恢复，不依赖内存或 `sessionStorage`；
- API、SSE、Renderer 和审批失败分别显示，不用一个通用错误覆盖所有故障来源。

## 5. 智能体管理与设置页冻结设计

### 5.1 列表页

列表页保留搜索、运行形态过滤、启停、复制、编辑和删除。每个智能体卡片显示：

- 名称、ID、运行形态和发布状态；
- 当前发布版本；
- 模型；
- 已绑定 Skill、Tool 和知识库数量；
- 人工确认策略；
- 最近更新时间和配置校验状态。

“工作空间目录”不得显示宿主机真实路径。页面只显示逻辑工作区策略，例如“Run 临时工作区”或“无持久工作区”。删除操作删除平台配置或归档版本，不直接删除宿主机目录。

### 5.2 编辑器信息结构

编辑器冻结为五个页签：

1. 基础配置：ID、名称、说明、运行形态、语言、启用状态；
2. 模型与提示词：供应商、模型、模型参数、系统提示词、终端上下文提示词；
3. Skills 与工具：Skill 绑定、所需 Tool、Tool 版本和授权状态；
4. 知识与资源：默认知识库、可访问业务资源及项目范围；
5. 运行与安全：执行模式、文件/代码/Shell/网络/子智能体能力、审批策略和资源限制摘要。

为控制第一期复杂度，可以沿用当前三页签组件实现，但字段归属必须遵循上述五个逻辑分组。未实现的分组以只读摘要或“尚未配置”显示，不提交虚假默认值。

### 5.3 执行模式

第一期智能体执行模式固定为 Deep Agents。页面不允许用户在 Deep Agents 和 LangGraph 之间为单个 Agent 任意切换：

- Deep Agents 是单智能体配置和运行框架；
- LangGraph 属于团队和流程编排页面；
- Agent 可以作为 LangGraph 节点引用，但 Agent 设置页不编辑图；
- 临时子智能体能力是 Deep Agents 的受限开关，不能创建未登记的长期平台 Agent。

### 5.4 Skill、Tool 和 MCP 边界

- Skill 描述任务方法、步骤和需要的工具；
- Tool Registry 提供权威 Tool ID、版本、Schema、风险等级和发布状态；
- Agent 显式绑定允许调用的 Tool；
- Tool Gateway 在运行时执行权限交集、参数校验、审批、限流、超时和审计；
- MCP 管理是 Tool 来源与连接管理，MCP Tool 同步、审核并发布到 Tool Registry 后才能被 Agent 绑定；
- Agent 设置页不直接保存 MCP Server 凭据，也不允许绕过 Tool Gateway 调用 MCP；
- 前端 Artifact/UI 能力同样以平台 Tool 授权，不登记 Vue/OpenLayers/Cesium 方法。

### 5.5 知识库与资源

- Agent 可绑定默认知识库集合，但运行时仍与用户、项目和团队权限取交集；
- Web 问答页可在允许范围内为单次会话缩小或补充知识库选择；
- 业务资源使用稳定资源 ID，不保存本地文件路径；
- Milvus 只负责向量检索，文档元数据、权限和版本记录在 PostgreSQL；
- 未完成索引、权限失效或版本不可用的知识库不能进入发布版本。

### 5.6 运行与安全配置

页面展示并保存平台允许的策略引用，不允许用户填写容器参数或宿主机目录：

- Workflow Runner 策略；
- Action Sandbox 策略；
- 文件读写能力；
- 代码执行能力；
- Shell 能力；
- 网络访问策略；
- stdio MCP 能力；
- 临时子智能体能力；
- 最大运行时间、并发、Token 和工作区容量；
- 人工确认策略。

文件、代码、Shell、网络、stdio MCP 和临时子智能体默认关闭。启用能力必须有相应 Tool 授权和沙箱策略；前端开关不能扩大后端权限。真实设备控制仍不在第一期授权范围。

### 5.7 草稿、校验与发布

- 编辑保存形成草稿，不立即改变正在运行的版本；
- 发布时生成不可变 AgentVersion；
- 发布前校验模型、Prompt、Skill、Tool、知识库、项目权限和沙箱策略；
- 发布失败显示字段级问题和验证摘要；
- AgentRun 保存使用的版本快照，后续配置修改不影响历史 Run；
- 复制智能体只复制配置引用，新副本默认停用且未发布。

## 6. 页面间边界

以下能力保持独立页面，不放入首批两个页面：

- LangGraph 团队与拖拽编排：`/collaboration`、`/workflow`；
- Tool Registry 与 Tool Gateway 管理：能力管理下的独立工具页面；
- MCP Server 连接与工具同步：`/mcp`；
- 沙箱实例、资源和故障监控：`/system/sandbox`；
- 全量 Artifact 检索与归档：`/artifacts`；
- GIS 专业工作区：由 `gis.map.2d` Artifact 按需进入；
- 审批任务汇总：`/approvals`。

AI 问答页只显示与当前 Conversation/Run 有关的摘要和操作；智能体设置页只配置 Agent，不承担团队图、平台工具发布或沙箱实例运维。

## 7. 前后端契约影响

会话基础阶段采用以下 REST/SSE 边界：

- `POST /api/conversations`；
- `GET /api/conversations`；
- `GET /api/conversations/{id}/messages`；
- `POST /api/conversations/{id}/messages`；
- `GET /api/agent-runs/{id}`；
- `GET /api/agent-runs/{id}/events`，使用 `Last-Event-ID` 续传。

后续执行阶段补充：

- Run 取消、重试和恢复；
- 审批读取与决策；
- Artifact读取、修订和 Action；
- 可选执行主体、知识库和资源的项目权限查询；
- Agent 草稿、校验、发布和版本读取；
- Agent Tool、知识库和沙箱策略绑定。

前端不得根据按钮点击自行制造完成事件。所有成功、失败、步骤、引用、审批和成果均来自持久化响应或 RunEvent。

## 8. 实施顺序

1. 按已提交的 Conversation、Message、AgentRun、RunEvent 与可续传 SSE 计划替换浏览器模拟状态；
2. 修正问答页状态文案，未接 Sandbox Executor 时只显示 `queued` 和“等待沙箱执行服务”；
3. 将智能体设置字段按本设计重新分组，保留后端已支持字段，新增字段先形成 API/数据模型计划；
4. 实现 Tool Registry 与 Tool Gateway 后开放 Tool 授权配置；
5. 实现 Workflow Runner、Action Sandbox 和 Deep Agents 后开放真实运行及安全状态；
6. 实现知识检索和 LangGraph 团队后开放引用和真实协同轨迹；
7. 实现 Artifact/GIS 后启用成果按钮和懒加载专业工作区。

首个可验收切片到第 2 步结束：用户能够创建和恢复项目私有会话，提交消息，看到真实的 `queued` Run 和可续传事件；页面不再显示模拟回答、模拟协同或虚假沙箱状态。

## 9. 验收标准

### 9.1 AI 问答页

- 刷新浏览器后会话和消息从服务端恢复；
- 同项目其他用户不能读取私人会话；
- 发送消息只生成一个持久化 Message 和 AgentRun；
- SSE 重连不重复展示事件；
- Sandbox Executor 未实现时不显示“沙箱已隔离”“正在分析”或伪造回答；
- 单智能体和团队提交正确的 `actor_type`/`actor_id`；
- 未知或失败的 Artifact Renderer 不影响对话；
- 桌面和移动视口中会话、消息、输入区和上下文不重叠。

### 9.2 智能体设置页

- Web、Desktop、Common 形态的提示词和说明一致；
- Skill、Tool、MCP、知识库和沙箱策略边界在界面中不混淆；
- 工作空间不显示或接受宿主机任意路径；
- 未实现能力不可提交为已启用；
- 草稿保存不影响已发布版本；
- 发布前校验失败可定位到具体配置；
- 后端不可用时页面阻止无效提交并提供重试；
- 删除和复制行为不直接操作宿主机文件。

## 10. 明确不在本轮实现

- 重做全局导航或视觉设计系统；
- LangGraph 拖拽画布；
- Deep Agents、LangGraph 或水利模型的实际执行；
- Tool Registry、Tool Gateway、Milvus、Sandbox Executor 的实现；
- OpenLayers/Cesium Renderer；
- 真实设备控制；
- 跨用户共享会话和团队 ACL；
- WebSocket 双向通信。

这些能力已有总体架构边界，分别进入后续独立设计和实施计划。
