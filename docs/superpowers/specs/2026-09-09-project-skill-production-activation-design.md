# Project Skill Production Activation Design

**Status:** Confirmed for implementation planning

**Date:** 2026-09-09

## Goal

将已经独立验收的 `/api/project-skills` 接入生产 FastAPI 应用，同时保持旧 `/api/skills`、前端和 Agent 运行时不变。发布过程必须具备默认关闭的功能开关、显式数据库迁移、幂等 Skill 桶初始化、启动时数据库版本校验和可逆的路由回退。

## Scope

本阶段包括：

- 在生产应用中按配置挂载现有 `project_router`。
- 增加 `IAP_PROJECT_SKILLS_API_ENABLED`，默认 `false`。
- 开关开启时，在后台调度器启动前校验数据库 Alembic revision。
- 将 API 容器启动时自动迁移改为显式的一次性 Compose 运维任务。
- 将现有 SQLite 到 PostgreSQL 的幂等迁移步骤移入同一个显式迁移任务。
- 增加幂等 Skill 对象存储桶初始化任务。
- 修复 `/api/project-skills` 请求验证中孤立 Unicode surrogate 导致的 HTTP 500，保持 422 响应结构。
- 为生产主应用、PostgreSQL、MinIO、Compose 和容器启动边界增加测试与运维文档。

本阶段不包括：

- 修改或停用旧 `/api/skills`。
- 旧目录数据与新 PostgreSQL/MinIO 数据之间的迁移或双写。
- 前端 Skill 页面切换。
- Agent、Team 或运行时读取新 Skill 版本。
- 下载、停用、删除、回滚发布、版本绑定、知识库、Embedding、GIS 或脚本执行。
- 远程服务器部署。

## Architecture

### Feature configuration

`app.core.settings.Settings` 增加只读布尔字段 `project_skills_api_enabled`，由 `IAP_PROJECT_SKILLS_API_ENABLED` 解析，缺省为 `false`。字段放在具有默认值的字段区域，避免破坏现有测试和显式构造器。

根目录 `.env.example` 和 `compose.yaml` 都显式列出该变量且默认关闭。`IAP_SKILL_BUCKET` 也在 Compose API 和桶初始化任务中使用同一个值，默认 `iap-skills`。

### Conditional route mounting

`app.main` 继续无条件挂载旧 `skills_router`。只有 `settings.project_skills_api_enabled` 为真时，才挂载现有 `app.skills.project_router.router`。开关关闭时：

- `/api/project-skills` 不在 `app.routes` 中。
- OpenAPI 不包含任何 `/api/project-skills` 路径。
- 对该路径的请求返回普通 404。
- 不执行 Project Skill 专用数据库启动校验。

开关开启时，新路由沿用现有 Cookie 身份、Origin、CSRF、`RequestContext`、项目权限和稳定错误码，不增加旁路认证。

### Database startup validation

新增窄模块 `app.skills.project_startup`，公开两个职责：

- 从随应用发布的 `alembic.ini` 和 `alembic/` 目录读取代码侧全部 Alembic heads。
- 从 `SessionFactory` 指向的数据库读取 `alembic_version.version_num` 集合，并与代码 heads 精确比较。

路径从模块文件位置解析：源码布局下定位到 `backend/`，容器布局下定位到 `/app`；两处都包含 `alembic.ini` 和 `alembic/`。不依赖进程当前工作目录。

当开关开启且出现以下任一情况时，校验抛出稳定的启动错误并使用 `raise ... from None` 阻止底层连接异常、数据库 URL 或凭据进入错误链：

- 无法读取代码迁移图。
- `alembic_version` 不存在或无法读取。
- 数据库 revision 集合与代码 head 集合不相等。
- 数据库不可达。

revision 标识本身不是秘密，版本不匹配错误可以展示排序后的 expected/actual revision；错误不得包含连接串、主机凭据或驱动异常文本。

`lifespan` 的顺序固定为：Runner Gateway 配置校验、Project Skill 数据库校验、启动 MCP 健康调度器。任何启动校验失败时调度器尚未启动。

MinIO 不属于全局启动门槛。对象存储短暂不可达不应拖垮不相关 API；涉及存储的 Project Skill 操作继续通过现有 `skill_storage_unavailable`/503 契约失败。

### Validation response hardening

生产应用注册一个 `RequestValidationError` 包装处理器：

- 非 `/api/project-skills` 路径直接委托 FastAPI 默认处理器，响应保持不变。
- Project Skill 路径仍返回状态 422 和 `{"detail": [...]}` 结构。
- 在 JSON 编码前递归处理错误明细中的字符串、字典键、列表、元组和 bytes；孤立 surrogate 或不可解码 bytes 使用 UTF-8 replacement 形成可编码值。
- 其他可由 `jsonable_encoder` 处理的对象保持原值，再交给结构化编码器。
- 不捕获业务处理中的未知异常，不把未知 500 改写成 422。

该处理器仅解决已复现的默认验证错误序列化失败。回归测试必须发送精确请求字节 `b'{"content":"\\ud800"}'`，断言 422、响应可解码、没有新 Skill/草稿/审计记录和对象写入。普通 API 的既有验证响应必须逐字段保持默认行为。

## Deployment Operations

### API image command

`backend/Dockerfile` 的默认命令只执行 Uvicorn。删除启动链中的：

- `python -m alembic upgrade head`
- `python -m app.migrations.sqlite_to_postgres`

API 进程不得创建或修改数据库结构，也不得自动导入旧数据。

### Explicit migration task

`compose.yaml` 增加 profile 为 `operations` 的一次性 `migrate` 服务。它复用 API 镜像和数据库/旧数据目录配置，按顺序执行：

```text
python -m alembic upgrade head
python -m app.migrations.sqlite_to_postgres
```

任一步失败时任务非零退出，第二步不得掩盖第一步失败。服务不自动重启，API 不依赖该服务，因此普通 `docker compose up` 不会隐式执行迁移。

### Skill bucket initialization task

新增 `app.skills.storage_bootstrap`，使用与 `create_default_skill_package_storage` 相同的 endpoint、access key、secret key、region 和 `IAP_SKILL_BUCKET`。命令行为：

1. `head_bucket` 成功时零退出，不修改桶。
2. 明确的 Not Found 时创建桶。
3. 创建竞争返回“当前凭据已拥有该桶”时视为成功，并再次确认可访问。
4. 无权限、网络错误、名称冲突或其他响应均以稳定、不含凭据的消息非零退出。

`compose.yaml` 增加 profile 为 `operations` 的一次性 `skill-storage-init` 服务。它复用 API 镜像和对象存储配置，不自动重启，API 不依赖该服务。

### Required rollout sequence

部署文档给出以下固定顺序：

1. 保持 `IAP_PROJECT_SKILLS_API_ENABLED=false`，构建并发布 API 镜像。
2. 显式运行 `migrate`，检查零退出。
3. 显式运行 `skill-storage-init`，检查零退出。
4. 使用只读查询确认数据库 revision 等于镜像代码 head。
5. 设置 `IAP_PROJECT_SKILLS_API_ENABLED=true` 并重启 API。
6. 确认 API 健康，OpenAPI 出现 `/api/project-skills`，未认证请求被拒绝，受权 Cookie 读取请求成功，缺少 CSRF 的写请求被拒绝。

不得把业务数据库用于降级测试，不在多实例 API 启动中运行迁移，不在日志或命令输出中打印密钥。

### Rollback

回退只执行以下动作：

1. 设置 `IAP_PROJECT_SKILLS_API_ENABLED=false`。
2. 重启 API。
3. 验证新路由消失且旧 `/api/skills` 仍可用。

不降级 Alembic、不删除 Skill 表、不删除对象桶、不删除已发布不可变版本。代码回退若需要单独进行，仍保留向前兼容的数据库结构。

## Data Flow

新接口启用后的请求路径为：

```text
HTTP request
  -> existing Cookie / Origin / CSRF middleware
  -> require_request_context
  -> ProjectSkillService
  -> PostgreSQL scoped repository
  -> MinIO only for create/save/import/publish package I/O
  -> AuditRecorder in the same successful database transaction
```

读请求不实例化对象存储。对象准备发生在短数据库写事务之外；成功审计与数据库变更仍原子提交。权限撤销继续在下一次认证请求生效，不新增操作中途线性化授权刷新。

## Error Contract

生产挂载不得改变已验收的 Project Skill 业务错误码：

- 401：`skill_authentication_required`
- 403：`skill_project_required`、`skill_permission_denied`，以及现有 Origin/CSRF 拒绝
- 404：`skill_not_found` 或开关关闭时普通路由 404
- 409：revision、幂等键和名称冲突
- 413：上传超限
- 422：请求、包或 manifest 验证失败，包括安全编码后的孤立 surrogate
- 503：运行时对象存储不可用

启动错误和运维任务错误不通过 HTTP 暴露。它们必须非零退出，消息可操作但不包含秘密。

## Verification Strategy

### Unit and application-boundary tests

- `Settings` 默认关闭，受支持真值启用，现有构造器兼容。
- 条件挂载在关闭时无路由/无 OpenAPI 路径，在开启时包含全部九个现有方法。
- 生产主应用真实使用 `project_router`，而不是复制路由或测试替身。
- 启动校验覆盖匹配、落后、超前、缺表、多 head 和连接失败，并证明错误不包含测试凭据。
- 校验失败发生在调度器启动前。
- Unicode 精确请求返回 422 且零数据库/对象变化；非 Project Skill 的默认 422 保持不变。
- Dockerfile 不包含迁移或旧数据导入命令。

### Deployment-task tests

- 使用结构化 YAML 解析验证 `migrate` 和 `skill-storage-init` 都属于 `operations` profile、无自动重启，API 不依赖它们。
- `docker compose config` 必须成功。
- 桶初始化 fake-client 测试覆盖已存在、创建、竞争、无权限和网络故障。
- 使用专用 UUID MinIO 桶执行真实幂等初始化，且只清理该测试创建的桶。

### Integration tests

- 新建本阶段专用 PostgreSQL 服务测试库和独立迁移测试库；不得复用业务库 `iap`。
- 迁移库先停在前一 revision，证明开关开启时启动校验失败；升级 head 后同一校验通过。
- 服务库升级 head 后，通过生产 `app.main` 和真实 Cookie 会话验证列表、创建及 CSRF/Origin 边界。
- 真实 PostgreSQL/MinIO 覆盖创建、保存、导入、发布和审计原子性，不把环境缺失 skip 当作通过。
- 运行现有旧 Skill API、身份、会话、审计、Agent、Team 和运行时聚焦回归。

### Final gates

- 后端完整 pytest 回归。
- `pip check`。
- Ruff、Black check 和 `git diff --check`。
- Compose 配置解析及 API 镜像构建。
- 在专用本地数据库和测试桶上依次执行两个运维任务，再以开关开启启动容器，完成健康、OpenAPI、认证和写保护检查。
- 明确记录外部配置 skip、警告和未执行的浏览器/前端验证。本阶段不以这些未执行项作为通过证据。

## Security and Compatibility

- 新接口不接受客户端指定的 unit、project、created_by、object key 或可信摘要。
- 任何新日志、异常或运维命令不得输出数据库密码、S3 secret 或完整连接 URL。
- 开关只控制新路由与其启动校验，不绕过认证或权限。
- 旧 API、前端、Agent、Team、运行时和历史数据保持不变。
- 数据库与对象存储变更均向前兼容；关闭开关无需破坏性回滚。

## Acceptance Criteria

满足以下条件才视为本阶段完成：

- 默认构建中 `/api/project-skills` 不可访问。
- 显式启用且数据库未到代码 head 时 API 拒绝启动。
- 显式迁移和桶初始化成功后，启用的生产主应用提供已验收的新 API。
- 真实 Cookie、项目权限、Origin 和 CSRF 行为符合既有契约。
- 孤立 Unicode surrogate 返回 422 且无数据或对象副作用。
- 旧 `/api/skills` 在启用和关闭新接口时均保持可用。
- API 容器启动不执行数据库迁移、旧数据导入或桶创建。
- 本地专用 PostgreSQL/MinIO 和容器验证通过，完整后端回归通过。
