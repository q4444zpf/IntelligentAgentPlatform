# 水利智能体平台总体架构设计

## 1. 目标与范围

平台以生产项目为起点，并保留向平台产品演进的能力。一期面向单个水利单位，支持多个项目、多个用户和多个角色，采用一套平台统一部署。首版优先形成智能体应用闭环，不建设复杂的水利模型运行平台，也不执行水库、闸门、泵站等真实设备控制。

现有水利模型代码继续独立运行，通过 MCP 协议向平台暴露工具。平台负责工具注册、发现、授权、参数校验、调用、结果归档和审计。

一期核心范围包括：

- 用户、角色、项目和项目成员权限；
- 大模型供应商与模型路由配置；
- Agent、Prompt、Skill、MCP、知识库和工作流管理；
- 单智能体问答与多智能体协同；
- 会话、消息、运行过程、引用和成果管理；
- 操作审计、工具调用审计和基础运行监控。

一期明确不包括：

- 完整多租户、计费和租户级资源配额；
- Kubernetes、服务网格和大规模微服务治理；
- 水利模型代码的统一上传、编译和容器调度；
- 设备控制指令的自动执行；
- 智能体无限递归创建智能体或无边界自主运行。

## 2. 架构决策

采用“模块化 FastAPI 核心 + 调度 Worker + 沙箱执行平面”的架构。平台仍作为一套产品统一部署，不在一期拆分大量微服务。

- FastAPI 核心负责同步业务 API、配置管理、权限校验、会话、运行建档和审计。
- 调度 Worker 负责版本快照、任务排队、执行包生成和 Sandbox Executor 调用，不承载 LangGraph、Deep Agents 或工具的实际执行。
- Sandbox Executor 为每个 Run 创建 Workflow Runner，并为高风险步骤创建 Action Sandbox。
- Deep Agents 作为单个智能体的标准运行框架，承载提示词、`AGENTS.md`、`SKILL.md`、工具、工作空间、上下文管理和临时子智能体委派。
- LangGraph 作为发布型多智能体团队和可视化工作流的编排运行时，承载显式节点、条件、并行、检查点、暂停和恢复。
- API 与调度 Worker 使用同一代码仓库和领域模型，通过任务队列和持久化运行状态协作。
- 小规模部署时 API、调度 Worker 和 Sandbox Executor 各运行一个实例；任务量增加时优先扩展 Sandbox Executor 容量和 Workflow Runner 并发。
- 只有当负载、团队所有权或独立发布周期形成明确边界时，才将领域模块拆为独立服务。

该方案按中型规模设计、小型规模部署，目标支持 20 至 100 名用户以及约 5 至 30 个并发智能体任务。

## 3. 系统分层

### 3.1 访问层

- Vue Web 管理控制台；
- AI 问答与协同工作台；
- 后续可复用同一 API 的嵌入式 Chatbox 和桌面客户端；
- Nginx 提供统一入口、静态资源服务和 API 反向代理；
- REST 用于业务操作，SSE 用于运行事件和增量回答。

### 3.2 平台核心

FastAPI 按领域模块组织，而不是按页面组织：

- `identity`：用户、角色、登录会话和权限；
- `projects`：项目、项目成员、业务资源和数据范围；
- `agents`：Agent、版本、Prompt、模型参数和发布状态；
- `teams`：协同团队、主管、成员、职责、可用工具和运行策略；
- `conversations`：会话、消息、附件和上下文；
- `runs`：Run、RunStep、事件、审批、引用和成果；
- `mcp`：MCP 服务、工具发现、Schema、白名单和授权；
- `knowledge`：知识库、数据源、文档、分块和索引任务；
- `workflows`：确定性流程定义与运行；
- `model_providers`：LLM 供应商、模型发现、默认模型和路由；
- `audit`：用户操作、模型调用和工具执行审计；
- `platform`：系统配置、健康检查和使用统计。

各模块通过明确的服务接口交互。禁止业务模块直接读取其他模块的内部存储实现。

### 3.3 Agent 执行平面

独立 Worker 负责运行建档、版本快照、DSL 校验、执行包生成和沙箱调度，不直接执行 LangGraph 图、Deep Agent、Shell 或用户代码。实际执行平面包含：

- Workflow Runner Sandbox：每个 Run 在独立受限容器中运行 LangGraph 和 Deep Agents，加载不可变执行包，负责节点调度、检查点、暂停和恢复；
- Deep Agent Runtime：在 Workflow Runner 内按平台快照创建智能体，加载 `AGENTS.md`、已绑定 Skill、MCP、知识库工具、运行上下文和人工确认策略；
- 协同编排器：主管 Deep Agent 规划、成员分派和结果汇总；
- LLM Gateway：统一模型调用、流式输出、重试、计量和追踪；
- RAG Runtime：权限过滤、查询改写、混合检索、重排和引用；
- MCP Executor：工具授权、Schema 校验、调用、超时和结果归档；
- Artifact Manager：将报告、表格、图件和模型文件登记为成果。

### 3.4 外部能力

- 云端或本地部署的 LLM 服务；
- 通过 Streamable HTTP 或 SSE 接入的水利模型 MCP 服务；
- 必须在隔离执行环境运行的本地 stdio MCP 服务。

## 4. 多智能体协同

一期采用“固定团队边界 + 主管智能体动态规划”。

平台同时支持三种运行模式：

1. 单智能体：直接运行一个 Deep Agent，由其按需加载 Skill、调用知识库和 MCP 工具。
2. 临时协作：主管 Deep Agent 使用 `subagents` 在授权白名单内临时委派子任务，适合开放式研判和报告生成。
3. 发布型协同：平台使用 LangGraph 编排多个已发布 Deep Agent，适合需要固定节点、并行会商、确定性校核、人工确认和失败恢复的生产流程。

团队配置固定以下内容：

- 主管智能体及其版本；
- 可参与的成员智能体及职责；
- 每个成员可使用的知识库、Skill 和 MCP 工具；
- 最大步骤数、最大并发数、最长运行时间和模型费用上限；
- 关键步骤、失败策略和人工确认策略。

主管智能体可以在上述边界内拆解任务、选择成员、安排串行或有限并行步骤，并汇总结果。主管不得临时扩大成员范围、访问其他项目数据、启用未授权工具或递归创建无限任务。

团队、Agent、Prompt、工具授权和模型参数均需版本化。Run 创建时保存执行快照，运行期间的配置修改不影响当前 Run。

Deep Agents 内部临时委派不能替代平台团队：临时子智能体不得成为未登记的长期业务资产，且其工具、知识库和项目权限不能超过主管与发起用户权限的交集。正式团队的成员、输入输出、执行状态和失败策略均由 LangGraph 图显式管理。

## 5. LangGraph 流程编排

平台提供拖拽式流程设计器，但不向前端暴露 LangGraph Python 代码。设计器保存平台自有的版本化 JSON DSL，发布时由后端校验并编译为 LangGraph `CompiledStateGraph`。

首版节点类型包括：

- 开始、结束和成果输出；
- Deep Agent 和多智能体团队；
- MCP Tool 和知识库检索；
- 条件分支、并行、汇合和循环上限；
- 数据映射与结构化转换；
- 人工确认和等待用户输入。

流程 DSL 至少记录节点 ID、节点类型、资源版本、输入输出 Schema、节点配置、边、条件和错误策略。Agent、Skill、MCP、知识库和团队均引用发布版本，不能仅引用可变名称。

发布流程依次执行：Schema 校验、图连通性检查、循环边界检查、输入输出兼容性检查、资源与项目权限检查、版本固化和 LangGraph 编译。只有发布成功的不可变版本可以在生产项目运行；草稿只能在测试环境试运行。

Skill 是 Deep Agent 按需加载的行为说明和资源包，不默认作为独立可执行节点。需要在确定性流程中直接执行的 Skill，必须声明结构化输入输出和允许调用的 Tool，由平台包装为受控节点。MCP Tool 和知识库检索属于实际可执行能力。

## 6. 运行数据流

1. 用户在 AI 问答中选择项目、执行主体、知识库和业务资源并发送消息。
2. API 校验用户、项目、Agent 或团队的访问权限，保存用户消息并创建 Run。
3. API 将 Run 标识放入 Redis 任务队列，立即向前端返回 Run ID。
4. Worker 领取任务，从 PostgreSQL 读取版本快照、权限、上下文和运行限制，生成不可变执行包并提交 Sandbox Executor。
5. Sandbox Executor 为当前 Run 启动独立 Workflow Runner。单智能体在其中进入 Deep Agent Runtime；临时协作由 Deep Agent 在白名单内委派；发布型团队或流程载入对应 LangGraph 版本。
6. 成员执行 RAG、LLM 或 MCP 步骤。每个步骤建立 RunStep 并持续写入 RunEvent。
7. MCP Executor 在调用前执行项目权限、Agent 工具权限和 JSON Schema 三重校验。
8. Redis 发布实时事件，API 通过 SSE 向前端推送规划、成员状态、工具进度、增量回答和成果事件。
9. 主管或单 Agent 汇总最终结果。消息、引用、工具轨迹和成果元数据写入 PostgreSQL，文件写入 MinIO。
10. Run 进入终态，前端显示完整执行轨迹和可追溯结果。

消息表示用户与系统的对话内容，Run 表示一次可取消、可重试、可追踪的执行。一个消息可以关联一个或多个 Run，但每个重试必须创建新的运行尝试记录。

Run 状态包括：`queued`、`planning`、`running`、`synthesizing`、`awaiting_input`、`succeeded`、`failed` 和 `cancelled`。

### 6.1 前端通信模型

智能体不得直接操作前端 DOM、Vue 组件实例或浏览器 API。前后端通过以下三类稳定契约通信：

- Message：用户和智能体的对话内容；
- RunEvent：规划、步骤、工具进度、增量回答、审批和错误等运行事件；
- Artifact：地图、图表、表格、报告和文件等可渲染、可交互成果。

服务端到前端使用 SSE，前端到服务端使用 REST。SSE 负责 `RunEvent` 和增量内容推送，REST 负责提交消息、页面上下文、地图选择、Artifact 操作和审批决定。普通问答不引入 WebSocket；只有后续出现高频双向协同编辑需求时才单独增加。

所有 RunEvent 使用统一事件信封，至少包含 `event_id`、`event_type`、`schema_version`、`run_id`、`step_id`、`sequence`、`timestamp` 和 `payload`。客户端按 `sequence` 顺序处理并保存最后事件序号，断线后通过 `Last-Event-ID` 续传。

首版事件类型包括：

- `run.status`、`run.completed` 和 `run.failed`；
- `message.delta` 和 `message.completed`；
- `step.started`、`step.progress`、`step.completed` 和 `step.failed`；
- `tool.started`、`tool.completed` 和 `tool.failed`；
- `approval.requested` 和 `input.requested`；
- `artifact.created`、`artifact.patch`、`artifact.ready` 和 `artifact.failed`。

### 6.2 Artifact 协议

Artifact 使用声明式、版本化 JSON，不包含可执行代码。基础结构至少包含：

```json
{
  "id": "artifact-456",
  "run_id": "run-123",
  "type": "gis.map.2d",
  "schema_version": "1.0",
  "revision": 3,
  "title": "清远河段洪水淹没分析",
  "status": "ready",
  "payload": {},
  "actions": [],
  "created_at": "2026-07-31T10:00:00+08:00"
}
```

`type + schema_version` 唯一确定前端 Renderer 和 JSON Schema。`revision` 必须单调递增，前端按 `artifact_id + revision` 对已有组件进行幂等增量更新。Artifact 元数据和修订记录保存在 PostgreSQL，大型内容和文件保存在 MinIO或专业数据服务。

平台首版支持 `gis.map.2d`、`gis.profile`、`chart.timeseries`、`table.data`、`report.document` 和 `file.download`。新增 Artifact 类型必须注册后端 Schema、权限策略、前端 Renderer和兼容策略，不能由 Agent 临时定义组件类型。

### 6.3 GIS Artifact

GIS 智能体通过 GIS MCP、数据服务或水利模型获得结果，然后生成 `gis.map.2d` Artifact。其 payload 至少包含坐标系、初始视图、图层列表、图例和允许操作：

```json
{
  "crs": "EPSG:4490",
  "view": {"center": [113.05, 23.7], "zoom": 10},
  "layers": [
    {
      "id": "inundation",
      "type": "geojson",
      "source": "/api/artifacts/artifact-456/layers/inundation",
      "style": {"fillColor": "#2563eb", "fillOpacity": 0.35}
    }
  ],
  "actions": ["zoom_to", "toggle_layer", "select_feature"]
}
```

允许的地图操作由平台枚举，包括定位、图层显隐、要素选择、时间播放和打开业务对象。Agent 不得下发 JavaScript、HTML、Vue模板、表达式代码或任意事件处理器。

GIS 数据按规模选择承载方式：

- 小型要素可以内联 GeoJSON，但必须受事件大小上限约束；
- 大型矢量使用矢量瓦片、分页 GeoJSON 或受控要素接口；
- 栅格成果使用 COG、WMS、WMTS 或瓦片服务；
- DEM 和三维数据后续使用地形服务或 3D Tiles；
- 图件、报告和下载文件使用短期签名 URL。

SSE 只传递元数据、状态和资源地址，不传输大型 GIS 数据。所有资源请求必须再次执行项目权限校验，不能仅依赖 Artifact 已通过鉴权。

### 6.4 页面上下文与双向交互

Web 智能体通过结构化 `ui.context` 接收页面上下文，包括项目、路由、坐标系、当前范围、可见图层、选中业务对象、数据时刻和前端支持的 Artifact 能力。前端不发送 DOM、组件内部状态、访问令牌或服务密钥。

用户在地图中选择水库、断面、河段或风险对象后，前端提交业务对象 ID和地图状态。API 校验用户、项目和对象权限，将事件保存并作为新的用户输入或当前 Run 的恢复输入。Agent 根据结构化上下文继续分析，但不能直接调用前端组件方法。

### 6.5 前端 Renderer 与懒加载

前端维护 Artifact Renderer 注册表，每个 Renderer 通过动态 `import()` 独立打包。收到 Artifact 时才加载对应组件和依赖：

```text
gis.map.2d       → OpenLayers 二维地图 Renderer
gis.scene.3d     → Cesium 三维场景 Renderer，后续启用
gis.profile      → 河道剖面 Renderer
chart.timeseries → 时序图 Renderer
report.document  → 报告预览 Renderer
```

首版二维地图明确采用 OpenLayers，满足水利项目常见坐标系、GeoJSON、WMS和WMTS需求。Cesium及其资源必须作为独立异步包，仅在收到 `gis.scene.3d` 时加载。

地图 SDK、Renderer和图层数据均需懒加载。前端可在收到 `artifact.created` 后预取组件代码，在 Artifact进入可视区域或用户打开成果时初始化地图和请求图层；组件卸载时必须释放地图实例、事件监听器、WebGL上下文和未完成请求。

### 6.6 Artifact 安全与兼容

- 后端在保存和推送前按 `type + schema_version` 执行 JSON Schema校验；
- URL只允许相对平台地址或已登记的GIS服务域名，禁止 `file:`、`javascript:` 和任意内网地址；
- 样式只允许声明式白名单字段，不允许脚本表达式；
- Artifact和图层接口执行单位、项目、用户和密级校验；
- MinIO签名URL短期有效，不能写入长期会话正文；
- 前端遇到未知类型或版本时显示安全的“不支持预览”状态，并允许下载原始成果；
- Renderer错误不能中断对话和Run事件消费，错误需关联 Artifact ID和 `trace_id` 上报。

## 7. 数据与存储

### 7.1 PostgreSQL

PostgreSQL 是结构化业务数据和运行状态的权威数据源，保存：

- 用户、角色、项目、成员和资源授权；
- Agent、团队、Prompt、Skill、MCP 和工作流配置及版本；
- 会话、消息、Run、RunStep、RunEvent 和审批记录；
- 知识库、文档、分块元数据和索引状态；
- 引用、Artifact、Artifact修订、成果元数据、模型调用与审计记录。

使用 SQLAlchemy 2 管理数据访问，使用 Alembic 管理数据库迁移。业务记录必须属于一个项目或单位级公共空间。项目数据隔离在服务层强制执行，不能仅依赖前端菜单或请求参数。

### 7.2 Milvus

Milvus 专门保存知识库向量和检索索引。向量实体至少携带：

- `unit_id`；
- `project_id`；
- `knowledge_base_id`；
- `document_id`；
- `chunk_id`；
- `security_level`；
- 文档版本和有效状态。

检索必须先应用单位、项目、知识库、角色和密级过滤，再进行向量召回。PostgreSQL 保存文档与分块的权威元数据，Milvus 索引允许删除并重建。

一期部署单节点 Milvus Standalone，通过 Docker Compose 管理，复用平台 MinIO，并部署 Milvus 所需的 etcd。后续只有在向量规模、吞吐或可用性要求达到瓶颈时才升级为 Milvus 集群。

### 7.3 Redis

Redis 用于：

- Celery 任务队列；
- 运行心跳、任务租约和取消信号；
- 短期事件发布与 SSE 转发；
- 有明确失效策略的缓存。

Redis 不保存永久会话或最终运行状态。Redis 数据丢失后，系统应能依靠 PostgreSQL 恢复权威状态。

### 7.4 MinIO

MinIO 保存原始文档、解析产物、用户附件、报告、表格、图件和水利模型成果。PostgreSQL 保存对象键、版本、校验和、内容类型、大小、所属项目和访问策略。

## 8. 技术选型

| 层次 | 技术 |
| --- | --- |
| Web | Vue 3、TypeScript、Vite、Ant Design Vue、Pinia、Vue Router、OpenLayers；Cesium按需扩展 |
| API | Python 3.11+、FastAPI、Pydantic、SQLAlchemy 2、Alembic、HTTPX |
| Agent 执行 | Deep Agents、LangChain、LangGraph、Celery、Redis、LangChain MCP Adapter |
| 业务数据库 | PostgreSQL |
| 向量数据库 | Milvus Standalone，一期单节点 |
| 对象存储 | MinIO |
| 入口与部署 | Nginx、Docker Compose |
| 测试 | Pytest、前后端集成测试、Playwright |

Deep Agents 用于实例化平台配置的智能体，原生映射系统提示词、`AGENTS.md` Memory、`SKILL.md` Skills、Tool、MCP、工作空间、上下文压缩、人工确认和子智能体。LangGraph 用于有状态的发布型协同图、可视化工作流、检查点和受约束分支；Celery 只负责将一次 Run 交给 Worker，不管理图内部节点状态。业务领域对象和流程 DSL 不直接依赖 LangGraph 内部数据结构，避免执行框架升级影响平台 API 和已保存流程。

## 9. 权限与安全

权限模型为“用户—角色—项目成员—资源授权”：

- 单位角色控制系统管理权限；
- 项目成员关系控制项目访问；
- 资源授权控制 Agent、团队、知识库、业务资源和 MCP 工具使用；
- 服务层在查询和执行前应用项目范围，禁止先取出越权数据再在响应层隐藏；
- Agent 权限不能超过发起用户权限，团队成员权限不能超过团队与用户权限的交集。
- Deep Agents 的文件、代码执行和子智能体能力默认关闭，按 Agent 版本显式授权；工作空间使用受限 Backend，不能直接访问宿主机任意路径。

密钥和认证头必须加密或通过外部 Secret 提供，API 响应和日志中只显示掩码。审计日志对 API Key、认证头、敏感文档内容和水利模型敏感参数进行脱敏。所有 MCP 调用记录发起用户、项目、Agent、Run、工具、脱敏参数、耗时、结果摘要和错误。

一期禁止执行真实设备控制。后续引入控制能力时，必须另行设计双人复核、人工确认、指令签名、回滚策略和控制网隔离，不复用普通 MCP 自动执行策略。

## 10. Deep Agents 与 LangGraph 运行沙箱

Deep Agents 和 LangGraph 均不得直接运行在 API、平台 Worker 或宿主机环境。每个 Run 的 LangGraph 与 Deep Agents 在独立 Workflow Runner沙箱中执行；文件操作通过虚拟 Workspace Backend；Shell、Skill 脚本、stdio MCP 和用户代码进入隔离级别更高的 Action Sandbox。

### 10.1 虚拟工作空间

Deep Agent 只看到以下虚拟目录：

| 虚拟目录 | 数据来源 | 默认权限 |
| --- | --- | --- |
| `/inputs` | MinIO 中当前 Run 的用户附件 | 只读 |
| `/skills` | 当前 Agent 版本绑定的 Skill | 只读 |
| `/knowledge` | KnowledgeService 返回的检索材料 | 只读 |
| `/workspace` | 当前 Run 独立的临时工作区 | 读写 |
| `/memories` | 经授权的长期记忆 Store | 受控读写 |
| `/outputs` | 当前 Run 的 MinIO 成果前缀 | 只允许创建当前 Run 成果 |

默认使用 Deep Agents `StateBackend` 保存临时工作文件，并通过自定义 Backend 将输入、Skill、记忆和成果映射到 PostgreSQL、MinIO 或平台服务。只有在独立沙箱容器内部才允许使用 `FilesystemBackend`，且根目录必须固定为当前 Run 工作目录并启用虚拟路径限制。

禁止将宿主机根目录、应用源码目录、数据库目录、容器运行时 Socket 或任意用户提供的绝对路径配置为 Agent Backend。Agent 不能看到 MinIO 对象密钥之外的服务器路径。

### 10.2 Shell 执行通道

Deep Agent 使用结构化 `execute_command` Tool 提交命令、参数、固定虚拟工作目录和超时，不在平台 Worker 或 Workflow Runner 内调用本地 `subprocess`。调用链为：

```text
Deep Agent 或 LangGraph 节点
→ Agent/用户/项目权限校验
→ 风险策略与人工确认
→ Sandbox Executor
→ 独立 Action Sandbox 容器
→ stdout、stderr、退出码和成果清单
```

命令请求必须使用可审计的程序名和参数数组，禁止通过 `shell=True` 拼接任意命令字符串。Sandbox Executor 校验命令白名单、环境变量白名单、工作目录和输入文件归属，执行结果限制大小并关联 Run、RunStep 和 `trace_id`。

### 10.3 容器隔离要求

沙箱容器必须满足：

- 使用非 root 用户和只读根文件系统；
- 仅挂载当前 Run 的临时目录，不挂载平台源码、业务数据库或宿主机目录；
- 不使用特权模式、宿主机 PID/IPC/网络命名空间或 Docker Socket；
- 默认关闭网络，需要联网的工具按目标域名、地址、端口和协议配置白名单；
- 限制 CPU、内存、磁盘、进程数、打开文件数、单文件大小和输出大小；
- 设置启动、执行和空闲超时，支持取消和强制终止；
- 清除宿主机环境变量，只注入当前任务所需的短期凭据；
- 任务结束后销毁容器和临时工作区，持久成果通过受控接口写入 MinIO；
- 记录镜像摘要、命令、脱敏参数、网络策略、资源用量、退出状态和阻断原因。

平台 Worker 和 Workflow Runner 均不得访问 Docker Socket。只有 Sandbox Executor 可以访问专用的 rootless Podman、containerd 或等效受限容器运行时；该运行时不得管理平台主服务容器。

### 10.4 分级执行策略

执行能力分为：

1. 图与智能体运行：LangGraph、Deep Agents、LLM、知识库和经审核的远程只读 MCP 在每个 Run 独立的 Workflow Runner 中执行。
2. 受信任 Skill：管理员审核的脚本进入受限 Action Sandbox，不在 Workflow Runner 进程中执行。
3. 不可信执行：导入 Skill 脚本、用户代码、文件解析和 stdio MCP 使用每个步骤独立的一次性 Action Sandbox，并按策略触发人工确认。

智能体版本必须保存文件权限、Shell 模式、网络策略、工作区配额、命令超时、CPU/内存限制、Skill 脚本权限、stdio MCP 权限和人工确认规则。默认策略为虚拟文件只读、`/workspace` 可写、Shell 关闭、网络仅允许平台已授权服务。

Deep Agents 的 Backend 路径限制和 LangGraph 的检查点、超时、人工确认属于应用层控制，不能替代容器级进程、文件系统、网络和资源隔离。只有 Workflow Runner 与 Action Sandbox 均成功启用并通过安全测试后，Web 页面才可以显示“沙箱已隔离”。

### 10.5 LangGraph Workflow Runner 沙箱

每个单智能体、多智能体团队和可视化工作流 Run 均创建独立 Workflow Runner 容器。该容器只运行平台签名版本的 Runner 镜像和由平台 DSL 编译的不可变执行包，不接受用户上传的 Python 节点、模块路径、镜像名称或启动命令。

Workflow Runner 必须满足：

- 非 root、只读根文件系统、独立进程和网络命名空间；
- 不挂载平台源码、宿主机目录、数据库数据目录或容器运行时 Socket；
- 只通过服务身份访问 PostgreSQL Checkpoint API、Redis 事件通道、LLM Gateway、KnowledgeService、MCP Gateway 和 MinIO 成果接口；
- 按 Run 注入短期凭据，凭据权限限定到当前单位、项目、用户、Run 和资源版本；
- 设置 Run 级 CPU、内存、网络、持续时间和最大并发节点限制；
- 暂停等待人工确认时持久化 LangGraph Checkpoint并停止容器，恢复时创建新容器从检查点继续；
- Run 完成、失败或取消后销毁容器，保留 PostgreSQL 中的状态、审计以及 MinIO 中的成果；
- LangGraph 自定义节点只能从平台预注册节点库选择，用户代码必须转换为 Action Sandbox节点执行。

Workflow Runner 是编排与推理沙箱，Action Sandbox 是命令与不可信代码沙箱。二者使用不同镜像、权限和网络策略，禁止 Workflow Runner 通过任何方式自行创建容器。

## 11. 错误处理与恢复

- LLM 限流和暂时性网络错误执行有限次数指数退避；参数、权限和业务校验错误不自动重试。
- MCP 调用设置连接超时、执行超时、最大结果大小和幂等键。
- 单个 RunStep 失败后，按团队策略终止、跳过非关键步骤或使用已有结果汇总；主管不得绕过权限或伪造缺失结果。
- Worker 使用心跳和任务租约识别进程异常。超时任务进入明确的失败或可恢复状态，禁止无边界重复执行。
- SSE 断开不取消 Run。前端使用 Run ID 和事件序号续传；最终状态以 PostgreSQL 为准。
- Milvus 异常时知识库进入降级状态。无法取得可靠引用时必须向用户明确提示，不生成伪造引用。
- MinIO、Milvus 和 Redis 均需健康检查；关键依赖不可用时 API 返回结构化错误码和追踪 ID。
- Sandbox Executor 不可用时，所有 Deep Agents 和 LangGraph Run 均停止创建或恢复；任何节点都不能回退到平台 Worker 本地执行。

## 12. API 边界

一期核心运行 API：

- `POST /api/conversations`：创建会话；
- `GET /api/conversations`：按项目查询会话；
- `POST /api/conversations/{id}/messages`：保存用户消息并创建 Run；
- `POST /api/agent-runs`：由其他业务入口直接创建 Run；
- `GET /api/agent-runs/{id}`：读取权威运行状态；
- `GET /api/agent-runs/{id}/events`：订阅 SSE 事件并支持事件序号续传；
- `POST /api/agent-runs/{id}/cancel`：请求取消运行；
- `GET /api/agent-runs/{id}/artifacts`：查询成果；
- `POST /api/agent-runs/{id}/inputs`：提交页面上下文、地图选择、补充输入或审批恢复数据；
- `GET /api/artifacts/{id}`：读取 Artifact 当前版本和渲染元数据；
- `GET /api/artifacts/{id}/revisions`：查询 Artifact 修订记录；
- `GET /api/artifacts/{id}/layers/{layer_id}`：经项目权限校验读取GIS图层或数据服务入口；
- `POST /api/artifacts/{id}/actions`：提交平台允许的Artifact交互动作；
- `POST /api/workflows`：创建流程草稿；
- `POST /api/workflows/{id}/validate`：校验流程 DSL；
- `POST /api/workflows/{id}/publish`：固化资源版本并编译 LangGraph；
- `POST /api/workflows/{id}/runs`：运行已发布流程版本；
- `POST /api/mcp/{client_key}/tools/{tool_name}/invoke`：经授权的内部工具调用入口，不直接暴露给普通前端用户。

API 统一返回业务错误码、用户可读信息和 `trace_id`。异步任务创建接口返回 `202 Accepted` 和 Run ID。

Sandbox Executor 仅提供内部服务接口，包括创建任务、读取状态、取消任务和读取成果清单。内部接口使用服务身份认证，不通过 Web 网关暴露，且不接受任意宿主机路径、挂载配置或容器运行参数。

## 13. 部署拓扑

Docker Compose 统一部署：

- `web`：Nginx 与前端静态资源；
- `api`：FastAPI 平台核心；
- `worker`：Celery调度 Worker，只生成执行包并提交沙箱任务；
- `sandbox-executor`：沙箱任务校验、容器生命周期和审计；
- `sandbox-runtime`：专用 rootless 容器运行时，不管理平台主服务；
- `workflow-runner`：由 Sandbox Executor 按 Run 动态创建的 LangGraph 与 Deep Agents 沙箱镜像；
- `action-sandbox`：由 Sandbox Executor 按高风险步骤动态创建的命令与代码执行沙箱镜像；
- `postgres`：业务数据库；
- `redis`：任务、事件和缓存；
- `milvus`：单节点向量数据库；
- `etcd`：Milvus 元数据依赖；
- `minio`：平台与 Milvus 对象存储。

每个服务配置健康检查、持久化卷、资源限制和重启策略。数据库、对象存储和 Milvus 数据目录必须纳入备份。生产环境通过环境变量或 Secret 注入凭据，仓库只保留安全占位配置。

## 14. 测试策略

### 14.1 单元测试

- 项目范围与角色权限计算；
- Agent、团队和工具授权交集；
- 团队版本快照和运行限制；
- MCP 参数校验、脱敏和错误分类；
- Run 状态转换和失败策略。
- 流程 DSL 校验、资源版本固化和 LangGraph 编译结果。
- 虚拟路径规范化、目录权限、命令策略、环境变量过滤和资源配额校验。
- RunEvent、Artifact和GIS图层Schema校验、版本兼容与修订幂等性。

### 14.2 集成测试

- PostgreSQL 事务与迁移；
- Redis 队列、取消、心跳和事件；
- Milvus 写入、过滤检索、删除和重建；
- MinIO 上传、签名访问和项目隔离；
- 模拟 MCP 服务的发现、调用、超时、错误和幂等行为；
- LLM Gateway 的流式响应、限流和重试。
- Deep Agent 的 Memory、Skill 渐进加载、工具授权和临时子智能体权限继承。
- LangGraph 检查点、并行汇合、人工中断、恢复和取消。
- Sandbox Executor 的任务创建、超时、取消、输出限制、网络白名单和成果回收。
- 验证沙箱无法读取宿主机、平台源码、其他 Run 工作区、Secret 和容器运行时 Socket。
- 验证每个 LangGraph Run 使用独立 Workflow Runner，且暂停后销毁、恢复时从 PostgreSQL Checkpoint继续。
- 验证 Workflow Runner 不能加载用户 Python节点、执行本地Shell或创建 Action Sandbox容器。
- SSE Artifact事件续传、乱序处理、重复事件幂等和大型数据不进入事件流。
- Artifact图层接口、MinIO签名URL和外部GIS服务的项目权限与域名白名单。

### 14.3 工作流测试

- 单智能体问答与引用溯源；
- 主管规划、成员协作和最终汇总；
- 非关键成员失败后的降级汇总；
- Worker 中断后的状态恢复；
- SSE 断线续传和页面刷新恢复；
- 用户、Agent 或团队尝试跨项目访问时被拒绝。
- Sandbox Executor 故障时所有 Deep Agents 和 LangGraph Run 失败关闭，且不会回退到本地执行。
- GIS Artifact 创建、图层增量更新、地图选择回传和Agent继续分析的完整闭环。

### 14.4 端到端测试

使用 Playwright 覆盖登录、选择项目、创建会话、切换单/多智能体、拖拽并发布流程、查看协同步骤、取消 Run、打开引用、懒加载GIS Artifact、地图交互回传和下载成果。桌面与移动视口均验证页面无重叠、状态清晰且长内容可阅读，并检查地图组件卸载后无遗留监听器和持续请求。

## 15. 演进路线

第一阶段完成 PostgreSQL、项目权限、真实会话与 Run 基础设施，并将现有 AI 问答页面接入 SSE。

第二阶段完成 Deep Agent Runtime、LangGraph Workflow Runner、虚拟 Workspace Backend、Action Sandbox、Sandbox Executor、LLM Gateway、Skill 渐进加载、MCP 工具真实调用和审计。

第三阶段完成 Milvus 知识库、引用溯源、Deep Agents 临时委派以及固定团队边界下的 LangGraph 协同。

第四阶段完成可视化流程 DSL、LangGraph 编译发布、成果管理、运行监控、备份恢复和生产压测。

第五阶段完成 Artifact协议、OpenLayers GIS Renderer、页面上下文回传、组件懒加载和GIS交互闭环；Cesium三维Renderer根据项目需求单独启用。

后续根据真实负载扩展 Worker、Milvus 和数据库，不预先拆分微服务。多单位租户、桌面客户端和设备控制均作为独立设计课题推进。
