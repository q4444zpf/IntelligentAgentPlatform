# Intelligent Agent Platform API

## 本地启动

```powershell
docker compose up -d postgres minio
$env:DATABASE_URL = "postgresql+psycopg://iap:iap@127.0.0.1:5432/iap"
$env:IAP_ALLOW_DEV_IDENTITY = "true"
cd backend
python -m pip install -r requirements.txt
python -m alembic upgrade head
python -m uvicorn app.main:app --reload --port 8000
```

`DATABASE_URL` 用于会话、消息、Agent Run 和 Run Event。修改模型后先创建 Alembic 迁移，再执行 `python -m alembic upgrade head`。

容器镜像的默认命令只启动 API，不再自动迁移数据库。首次部署及每次包含数据库迁移的升级，都必须先按[项目 Skill 生产激活手册](../docs/deployment/project-skill-production-activation.md)构建镜像并显式运行一次 `migrate` 操作；普通 `docker compose up` 不会执行迁移。

`IAP_ALLOW_DEV_IDENTITY` 默认是 `false`。开启后，请求仍必须同时提供 `X-Unit-ID`、`X-User-ID` 和 `X-Project-ID`；这只是本地开发适配器，不得作为生产认证。生产环境应使用可信认证会话，并保持该开关关闭。

根目录 Compose 同样默认关闭开发身份。需要在容器化本机环境调试会话或审计页面时，必须同时显式设置 `IAP_ALLOW_DEV_IDENTITY=true`、`VITE_DEV_UNIT_ID`、`VITE_DEV_USER_ID`、`VITE_DEV_PROJECT_ID` 和 `VITE_DEV_USER_ROLES` 并重新构建 Web 镜像；`VITE_DEV_USER_ROLES` 是逗号分隔的 `user`、`project_admin`、`unit_auditor` 集合。这些变量只用于非敏感测试身份，不得用于生产部署。

## 首个单位管理员

数据库迁移完成后，在离线维护窗口执行一次 bootstrap。默认命令逐项交互读取单位、初始项目、显示名以及原始 OIDC issuer/subject，不创建普通用户密码：

```powershell
cd backend
python -m app.identity.bootstrap
```

自动化环境可改用仅限执行账号读取的 JSON 文件，并通过 `--request-file` 传入。文件必须恰好包含 `unit_code`、`unit_name`、`user_display_name`、`issuer`、`subject`、`initial_project_code` 和 `initial_project_name`；不要加入密码或令牌。Windows 上应先移除继承权限并只向执行账号授予读取权限，POSIX 上文件模式必须为 `0600`：

```powershell
python -m app.identity.bootstrap --request-file .\bootstrap-request.json
```

命令在一个事务中创建单位、项目、成员关系、原始外部身份绑定、内置授权目录和脱敏安全审计；任何失败都会整体回滚。issuer 与 subject 按输入原文保存，不会修剪、折叠大小写或规范化末尾斜杠。

## 会话和运行接口

- `POST /api/conversations`
- `GET /api/conversations`
- `GET /api/conversations/{conversation_id}`
- `GET /api/conversations/{conversation_id}/messages`
- `POST /api/conversations/{conversation_id}/messages`
- `GET /api/agent-runs`
- `GET /api/agent-runs/{run_id}`
- `GET /api/agent-runs/{run_id}/events`
- `GET /api/agent-runs/{run_id}/tool-invocations`
- `POST /api/artifacts`
- `GET /api/artifacts`
- `GET /api/artifacts/{artifact_id}`
- `GET /api/artifacts/{artifact_id}/download`
- `POST /api/runs/{run_id}/artifacts/{artifact_id}`

## 多智能体 Team 接口

- `GET/POST /api/collaboration/teams`
- `GET/PATCH /api/collaboration/teams/{team_id}`
- `PUT /api/collaboration/teams/{team_id}/draft`
- `POST /api/collaboration/teams/{team_id}/publish`
- `POST /api/collaboration/teams/{team_id}/enable`
- `POST /api/collaboration/teams/{team_id}/disable`
- `GET /api/collaboration/teams/{team_id}/versions`
- `GET /api/collaboration/teams/{team_id}/versions/{version}`

接口按当前单位和项目隔离。读取、管理、运行分别要求 `collaboration.read`、`collaboration.manage`、`collaboration.run`；跨项目资源使用安全的不存在响应。版本 `0` 是仅管理者可读写的草稿，正整数版本是发布后不可变的执行定义。发布要求恰好一个主管、至少一个不同成员及合法运行上限；Team 只有发布后才能启用。

会话消息使用 `actor_type="team"` 和 Team `actor_id` 创建 Team Run。服务在同一接收流程中解析并记录 `actor_version_id`，后续编辑或重新发布不会改变已接受 Run。停用 Team 只阻止新 Run；已在运行的成员调用仍由 Runner Gateway 按用户、Team、Agent、成员白名单和能力当前状态的交集重新授权。

事件接口返回有限 SSE 回放并关闭连接。通过 `Last-Event-ID` 请求头传入已处理的事件序号即可恢复读取；持续事件流将在沙箱执行服务接入后实现。

`GET /api/agent-runs` 仅查询请求身份所属的当前项目和当前用户数据，支持 `page`、`page_size` 分页（`page_size` 最大为 100），以及 `status`、`actor_id`、`query`、`started_after`、`started_before` 筛选。响应包含当前筛选范围的分页记录、总数和状态/工具调用汇总；`query` 匹配会话标题或 Run ID，`started_after` 和 `started_before` 必须使用包含 `Z` 或明确时区偏移的 timezone-aware ISO 8601 时间，且须满足 `started_after <= started_before`。

运行详情继续复用既有的 `GET /api/agent-runs/{run_id}`、`GET /api/agent-runs/{run_id}/events` 和 `GET /api/agent-runs/{run_id}/tool-invocations`。这些接口沿用相同的项目与用户范围隔离；工具调用接口返回 Tool Gateway 按敏感字段名脱敏后持久化的 `arguments_summary`、`result_summary` 及错误码、耗时等审计字段。调用方仍须避免把秘密放在非敏感字段中，审计接口不应作为秘密存储。

生产 Dispatcher 在 MinIO 可用时会将最终文本结果保存为当前 Run 的 `run-result.txt` Artifact；二进制内容只进入对象存储，不写入 RunEvent 或审计元数据。对象存储依赖未安装时，运行仍可完成，但不会生成成果文件。

### LangGraph / Deep Agents Runtime

平台已提供 `app.runtime.langgraph_runtime.LangGraphRuntimeAdapter` 作为统一运行边界。它为每次 Run 创建带 `thread_id=run_id` 的 LangGraph 配置和状态，提取最终助手消息；工具授权、审批、审计和 Artifact 持久化仍由平台服务负责，图节点不得直接连接 MCP 或对象存储。

当前默认 Dispatcher 仍使用已验证的兼容 Harness。待 Workflow Runner 沙箱和 LangGraph checkpoint 存储启用后，再通过运行配置注入编译后的图和 Deep Agents 实例，不改变现有 API、权限和 RunEvent 契约。

`app.runtime.deepagents_factory.DeepAgentFactory` 已提供从已发布 Agent/Tool 快照创建 Deep Agent 的边界。创建器只接收 `published=true` 且 `enabled=true` 的工具，不能通过 Agent 快照扩大工具权限；真实 `create_deep_agent` 仅在 Workflow Runner 依赖完整时启用。

`app.runtime.tool_gateway_adapter.ToolGatewayAdapter` 将 Deep Agent 工具调用转换为平台 `ToolCall`，所有调用继续经过 ToolGateway 的授权、Schema 校验、审批、MCP 路由、审计和 RunEvent。`app.runtime.deepagent_node.DeepAgentNode` 是 LangGraph 节点包装器，只处理消息状态，不直接访问数据库、MCP 或 MinIO。

`runtime_checkpoints` 表和 `CheckpointStore` 为 LangGraph 状态提供按 Run 隔离的 JSON 检查点，`LangGraphRuntimeAdapter` 可在执行前恢复最新状态、执行后幂等保存。检查点不允许写入二进制、凭据或原始敏感数据；当前仅在 Workflow Runner 集成时启用，默认 API Dispatcher 不直接运行图。

现有审批链已接入 Checkpoint 生命周期：工具要求审批时保存 `waiting_approval` 状态，审批通过后 Dispatcher 从同一 Run 继续执行，并写入 `completed` 状态。审批决定仍由 ApprovalService 校验，Checkpoint 不能绕过审批或扩大工具权限。

Compose 已包含独立 `workflow-runner` 服务边界，并使用专用 `backend/Dockerfile.runner` 镜像。其 `/health` 会分别报告进程健康和 `sandbox` 能力；当前默认 `sandbox=false`，`POST /runs` 必须返回 503。镜像以非 root 用户启动，不执行 API 数据库迁移；容器使用只读文件系统、丢弃 Linux capabilities 并禁止提权，但这只是服务边界加固，不能等同于已完成的按 Run 隔离 Sandbox Executor。

`SandboxExecutor` 已实现受控操作执行基础：启用后为每个 Run 创建临时工作区，支持超时和 finally 清理，并只允许服务端注册的操作名；它不接受 Shell 字符串或用户上传代码。当前 Compose Runner 仍保持 `sandbox=false`，等待真正的 per-Run 容器、资源/网络限制和清理验收完成后再开启。

受控 Launcher 服务不向 Workflow Runner 暴露 Docker Socket，仅提供按 Run 限定的容器生命周期接口：`/health`、创建、检查、终止和清理。请求必须同时携带 `Authorization: Bearer <IAP_RUNNER_LAUNCHER_TOKEN>` 与匹配路径的 `X-Run-Id`；未配置令牌时服务返回 `503`。Launcher 必须使用 `ContainerPolicy`，在真实容器检查和 staging 安全验收完成前保持 `sandbox=false`。

staging 启动 Launcher 时必须通过部署环境注入一次性或密钥管理器托管的 `IAP_RUNNER_LAUNCHER_TOKEN`；未配置令牌时 profile 会拒绝启动，不允许降级为匿名服务。Launcher 镜像因需要访问受控 Docker Socket 以 root 运行，但仅在 `sandbox` profile 中挂载 Socket；Workflow Runner 始终以非 root 运行且无 Socket。

当同时配置 `IAP_SANDBOX_LAUNCHER_URL` 和 `IAP_RUNNER_LAUNCHER_TOKEN` 时，Workflow Runner 会在接受 Run 前调用 Launcher 创建并检查当前 Run 容器；创建失败、检查失败或容器非 running 都会返回 503，并清理已创建容器。未配置令牌时该客户端保持关闭。

内部运行时 HTTP 客户端通过 `app.runtime.http_tls` 在可取消等待的工作线程中读取 certifi 并构造验证上下文，保留证书和主机名校验，不采用环境代理或环境 CA 覆盖。真实 Workflow 提交的健康检查与 POST 共用一次异步操作、一个 TLS 上下文和一个 HTTPX 客户端；操作结束后关闭客户端，不在不同提交间共享可变上下文。显式绝对截止时间覆盖初始化及两阶段请求，健康检查失败或截止时间已耗尽时不发送 POST；未传截止时间时保留各请求的默认预算。自定义传输继续走原有兼容调用路径。

Launcher 使用 `IAP_SANDBOX_RUNNER_IMAGE` 指定执行镜像，默认必须是可信 `iap/` 前缀并包含 tag；镜像不应由请求方传入。

Per-Run 容器策略已固定为可信 `iap/` 镜像、非特权、只读根文件系统、`cap_drop=ALL`、`no-new-privileges`、网络关闭、512MB 内存和 128 PID 上限。`ContainerLauncher` 只接受服务端生成的策略和工作区路径，运行结束强制删除容器；Docker client 不可用时直接拒绝，不回退到宿主机执行。

Runner 的 `sandbox=true` 现在还需要六项 readiness 同时为真：可信镜像、非 root、只读根文件系统、网络关闭、资源限制和强制清理。Compose 默认全部为 `false`；健康接口会返回缺失项，执行接口在任一条件缺失时返回 503。

`SandboxInspector` 已支持从 Docker inspect 风格配置实际计算 readiness，并额外校验 `Privileged=false` 与 `CapDrop=ALL`。当前 Runner 尚未连接受控 Docker/CRI inspect 通道，因此仍报告 `sandbox=false`；不能仅通过设置环境变量宣称沙箱安全。

`DockerInspectTransport` 只提供只读 `containers.get(...).attrs` 查询，未暴露创建、启动、执行或删除能力。Runner 未挂载 Docker Socket；生产部署应由外部受控 Launcher 提供 inspect 结果，查询失败时健康接口增加 `container_inspection` 缺失项并继续拒绝运行。

## 统一审计接口

- `GET /api/audit/events`
- `GET /api/audit/events/{event_id}`
- `GET /api/audit/events/{event_id}/related`

列表支持 `page`、`page_size`（最大 100）、`category`、`source`、`action`、`status`、`risk_level`、`project_id`、`user_id`、`query`、`occurred_after` 和 `occurred_before`。`query` 匹配事件、Trace、Run 或资源标识；时间边界必须是带 `Z` 或明确偏移的 timezone-aware ISO 8601 值，且开始时间不得晚于结束时间。

读取范围由服务端角色约束：`unit_auditor` 可读当前单位，`project_admin` 可读当前项目，普通 `user` 仅可读当前项目中的本人事件；客户端筛选不能扩大该范围。详情和关联事件对“不存在”与“越权”统一返回安全 404，避免暴露资源是否存在。

审计事件和 Agent 运行使用排序稳定的 `actor_roles` 角色代码数组作为发生时快照；历史身份无法可靠还原时保存空数组，不得伪造管理员角色或写入 Agent 的 `actor_type`。审计事件同时持久化 `authorization_scope` 与 `event_scope`；平台事件不带单位/项目，单位事件只带单位，项目事件同时带单位和项目。认证事件使用 `category=security`、`source=auth`，并可记录 `auth_method`。

审计记录是追加写入的独立事件，不提供更新或删除 API。写入前仅保留显式允许的元数据字段，并按敏感字段名递归脱敏、限制层级与大小；不得将凭据、口令、客户数据或原始 Prompt 写入摘要或元数据。当前已接入 Agent、tool、LLM 运行事件及 Agent、MCP、Tool、模型供应商等选定管理操作。知识库真实审计、真实 MCP 执行、沙箱审计、导出、保留期自动化和事件总线尚未实现。

升级统一审计表后，运维人员可在 API 启动前或独立维护窗口显式执行受控回填：

```powershell
cd backend
python -m alembic upgrade head
python -m app.audit.backfill
```

该命令按批提交，并以每个 Agent Run 的稳定幂等键写入快照，可安全重跑；API 启动不会自动回填。仅对目标测试或维护数据库执行，并在执行前确认 `DATABASE_URL`。

## 大模型供应商配置

模型供应商、智能体和 MCP 配置与会话运行数据统一保存在 `DATABASE_URL` 指定的 PostgreSQL。内置供应商定义位于 `app/model_providers/registry.py`。

旧版 SQLite 数据可通过以下命令一次性导入；默认读取 `/data/model-providers.db`、`/data/agents.db` 和 `/data/mcp.db`：

```powershell
python -m alembic upgrade head
python -m app.migrations.sqlite_to_postgres
```

可通过 `LEGACY_SQLITE_DATA_DIR` 或三个 `LEGACY_*_DATABASE` 环境变量调整源文件路径。导入按数据域幂等执行，只导入空的 PostgreSQL 目标表，不删除 SQLite 文件。

## Skill 文件包与版本基础

新增内部模块 `app.skills.package`、`app.skills.package_storage` 和 `app.skills.repository`：

- 解析器验证 ZIP 路径、条目类型与限额，返回不可变文件清单和内容摘要，不解包到宿主目录、不执行脚本。
- 存储适配器写入独立对象，以有界读取和 SHA-256 验证完整性；`IAP_SKILL_BUCKET` 默认为 `iap-skills`，调用方需准备该桶。连接信息沿用 `IAP_OBJECT_STORAGE_*`，不要把凭据写入 Skill 文件。
- 仓储按单位和项目查询，管理草稿 revision、不可变发布版本和发布幂等记录。调用方先预分配 Skill UUID，上传后使用同一 ID 创建资源；调用方负责授权、发布前重新验证对象以及提交或回滚事务。
- 迁移 `20260908_26` 新增三张 Skill 表和 PostgreSQL 版本不可变保护，不自动导入现有本地 Skill 目录。

这些模块尚未接入现有 `/api/skills`、Agent/Team 绑定和前端界面。项目权限 API 已提供下述隔离测试装配；生产挂载、运行时版本捕获及旧数据切换属于后续交付，不能仅部署本迁移就视为完成 Skill 生产化。

从仓库根目录运行聚焦测试：

```powershell
python -m pytest backend/tests/skills -q
python -m pytest backend/tests/integration/test_skill_versions_postgres.py backend/tests/integration/test_skill_package_minio.py -q
```

真实集成测试需要专用 `TEST_DATABASE_URL`，以及 `TEST_S3_ENDPOINT`、`TEST_S3_ACCESS_KEY`、`TEST_S3_SECRET_KEY`。PostgreSQL 测试前需在该测试库升级到当前迁移版本；MinIO 测试只创建和回收自己的临时桶。没有这些变量时集成测试会跳过，跳过不代表通过。不要指向业务数据库；不可变版本测试记录可在专用测试数据库整体回收时清理。

### 项目 Skill 隔离接口

`app.skills.project_router.router` 只在测试应用中装配，`app.main` 未导入或挂载，不存在生产启用开关。旧 Skill 接口的认证风险本轮未整改，Web UI、运行时、Agent/Team、业务数据和本地 Skill 目录均未切换或双写。

| 方法 | 路径（前缀 `/api/project-skills`） | 权限与结果 |
| --- | --- | --- |
| GET | 空路径 | `skill.read`，分页摘要 |
| GET | `/{skill_id}` | `skill.read`，资源摘要 |
| GET | `/{skill_id}/draft` | `skill.read`，含正文草稿 |
| GET | `/{skill_id}/versions` | `skill.read`，版本摘要分页 |
| GET | `/{skill_id}/versions/{version_id}` | `skill.read`，含正文固定版本 |
| POST | 空路径 | `skill.manage`，创建 revision 1 草稿，201 |
| PUT | `/{skill_id}/draft` | `skill.manage`，按 expected_revision 保存，200 |
| POST | `/import` | `skill.manage`，ZIP 批量 create/update/skip，200 |
| POST | `/{skill_id}/publish` | `skill.manage`，复验并幂等发布，200 |

身份来自有效会话，必须显式选择当前单位下的活动项目；不接受客户端身份头替代会话。不登录为 401，无所选项目或入口权限为 403，资源不在该权限的项目/owner 范围内统一为 404。`own` 授权在 SQL 查询及分页 total 中过滤。测试应用复用真实 Cookie 写保护，校验 CSRF 请求头和 Origin；读响应及含正文的写响应均 `Cache-Control: no-store`。该验证不代表完成旧接口安全改造。

创建仅接收 `content`；保存接收 `content`、严格正整数 `expected_revision`；发布仅接收 `expected_revision`，并要求 `Idempotency-Key` 为 1–128 个可打印 ASCII 字符，原样保留空格。客户端不能指定版本号、对象位置、摘要或所有者。已提交同键同请求直接返回原版本，即使后来保存草稿或对象存储不可用；每次重放仍重新授权，同键不同 revision/发布者为 409。

JSON 正文上限 200,000 字符。ZIP 上限 10 MiB，实际解压内容 20 MiB、500 条目；multipart 的可选 manifest 必须是非空 JSON 文本，最多 500 项且 UTF-8 编码不超过 256 KiB，明确指定每个源 Skill 的 create/update/skip。列表默认 offset 0、limit 20，limit 1–100，q 最长 120 字符且按名称字面包含匹配。上传超限返回 413；参数/包格式非法返回 422；名称、revision 或幂等冲突返回 409；对象读取、摘要或完整快照复验失败返回 503。未知内部异常保留为通用 500。

对象 I/O 不持有数据库会话或行锁。发布在短写事务中重新授权、锁定资源、比较完整已验证草稿快照，版本、发布指针、revision 消耗及成功审计原子提交。数据库提交失败后已上传但未被引用的对象可暂留；本模块不自动删除对象、桶或共享数据，孤立对象补偿需由后续受控运维流程处理。

权限迁移 `20260908_27` 仅为内置 `project_admin` 角色补充缺失的 `skill.manage` / `project` 授权，依赖已有的 `skill.manage` 权限目录项，不新增权限目录项或修改其他角色。降级保留已发放的授权，不做删除式回滚，也不迁移旧 Skill 数据。

在专用测试环境从仓库根目录验证：

```powershell
$env:PYTHONPATH = "backend"
python -m pytest backend/tests/skills/test_project_publish.py backend/tests/skills/test_project_api.py backend/tests/skills/test_project_cookie_api.py backend/tests/skills/test_repository.py -q -p no:cacheprovider
python -m pytest backend/tests/integration/test_skill_control_plane_postgres.py backend/tests/integration/test_skill_control_plane_minio.py backend/tests/integration/test_skill_versions_postgres.py -q -p no:cacheprovider
```

普通测试清除外部 `TEST_*`，中间件测试的 `DATABASE_URL` 仅指向专用服务测试库 `iap_skill_control_test_20260908_a`；服务集成还需将 `TEST_DATABASE_URL` 指向同一库，迁移前置状态为 `20260908_27`。权限迁移升降级使用独立迁移库，禁止对服务库运行整个可降级迁移套件。真实集成使用 PostgreSQL 独立连接与 Event/Barrier 验证竞争；MinIO 使用本次创建的 UUID 桶并回收自身对象。已提交不可变版本所在的 UUID 测试范围留在专用数据库，不关闭保护触发器；该库可由后续显式环境回收流程整体回收。

## 测试

```powershell
cd backend
python -m pytest -q
```

Team 聚焦回归与根级验收：

```powershell
cd ..
$env:PYTHONPATH = "backend"
python -m pytest backend/tests/collaboration backend/tests/runtime/test_team_graph.py tests/e2e/test_published_team_run.py -q
```
