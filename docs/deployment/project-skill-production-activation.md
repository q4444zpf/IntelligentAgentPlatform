# 项目 Skill 生产激活

本手册用于首次部署项目 Skill 生产接口。数据库迁移是显式运维操作，API 容器只启动 Uvicorn，不负责改变数据库结构或导入旧数据。

## 首次部署

1. 在部署环境配置 `DATABASE_URL`、`IAP_OBJECT_STORAGE_ENDPOINT`、`IAP_OBJECT_STORAGE_ACCESS_KEY`、`IAP_OBJECT_STORAGE_SECRET_KEY`、`IAP_OBJECT_STORAGE_REGION`（默认 `us-east-1`）和 `IAP_SKILL_BUCKET`（默认 `iap-skills`），并保持 `IAP_PROJECT_SKILLS_API_ENABLED=false`。确认数据库和桶属于目标部署环境后再继续。凭据使用环境变量或秘密管理服务，不写入版本库或操作日志。
2. 构建并标记本次 API 镜像：

   ```powershell
   docker compose build api
   docker image inspect intelligent-agent-platform-api:local
   ```

3. 在只运行一个迁移作业的维护窗口执行：

   ```powershell
   docker compose --profile operations run --rm migrate
   if ($LASTEXITCODE -ne 0) { throw "Project Skill database migration failed" }
   ```

   必须检查命令退出码。只有退出码为 `0` 时才能继续；失败时保持功能开关关闭并排查迁移日志。
4. 验证数据库的 `alembic_version` 是当前镜像唯一的 Alembic head：

   ```powershell
   docker compose --profile operations run --rm --no-deps migrate python -m alembic current
   docker run --rm intelligent-agent-platform-api:local python -m alembic heads
   ```

   两条命令必须各自只报告同一个 revision。`migrate` 随后运行的 SQLite importer 只处理 model providers、Agents 和 MCP clients，不导入 Skill 数据；检查这些实际类别的导入结果。不得用 API 容器启动成功代替 revision 和 importer 验证。
5. 使用本次 API 镜像显式初始化 Skill 桶：

   ```powershell
   docker compose --profile operations run --rm skill-storage-init
   if ($LASTEXITCODE -ne 0) { throw "Project Skill storage initialization failed" }
   ```

   作业先检查桶；仅缺桶时创建，并在创建后再次检查。已有可访问桶和同账户并发创建可重复执行；其他账户占用、权限不足、连接错误或创建后检查失败均返回非零退出码，并只输出稳定错误行 `unable to initialize project skill bucket`。默认区域不发送 `LocationConstraint`，其他区域使用配置值。失败时保持功能关闭，检查目标 endpoint、区域、桶归属和授权后重试。禁止为了重试删除已有桶或其中对象。
6. 数据库迁移、桶初始化和验证均成功后，将 `IAP_PROJECT_SKILLS_API_ENABLED=true` 注入 API 服务并启动或滚动更新服务。

7. 对新实例执行健康、路由和认证冒烟检查：

   ```powershell
   $task6BaseUrl = "https://platform.example"
   (Invoke-RestMethod "$task6BaseUrl/api/health").status
   $task6OpenApi = Invoke-RestMethod "$task6BaseUrl/openapi.json"
   $task6OpenApi.paths.PSObject.Properties.Name -contains "/api/skills"
   $task6OpenApi.paths.PSObject.Properties.Name -contains "/api/project-skills"
   (Invoke-WebRequest "$task6BaseUrl/api/project-skills" -SkipHttpErrorCheck).StatusCode
   ```

   依次要求 `ok`、`True`、`True` 和未携带 Cookie 时的 `401`。随后用受控测试账号的真实会话 Cookie 读取当前项目列表，并用匹配 Origin 与 CSRF header 创建一次可回收的测试 Skill；确认响应 `201`、对象键位于 `unit/project/skill/` 前缀且只有一条 `skill.create` 成功审计。Cookie、CSRF 值和对象存储凭据不得写入命令历史或报告。

普通 `docker compose up` 不会运行迁移。多实例部署中不得让任何 API 容器执行 Alembic；迁移只能由上述单次 `migrate` 操作完成，以免多个实例并发修改结构。

`skill-storage-init` 同样只属于 `operations` profile，使用与 API 相同的 endpoint、访问凭据、区域和桶配置。API 不依赖任何运维作业，启动成功不能代替初始化作业退出码验证。

## 回滚

应用回滚时先将 `IAP_PROJECT_SKILLS_API_ENABLED=false`，再回滚 API 镜像或停止项目 Skill 流量。数据库迁移只向前处理，回滚绝不执行 `alembic downgrade`，也不得删除或重建数据库卷。若新版本无法继续使用，应保持功能关闭，保留数据库与迁移日志，并通过经审核的向前修复迁移恢复服务。

仅关闭 `IAP_PROJECT_SKILLS_API_ENABLED` 即可从 OpenAPI 和流量入口移除 `/api/project-skills`；旧 `/api/skills` 继续可用。不要在回滚中删除 Skill 对象、数据库行或桶。

本次激活不包含前端/浏览器接入、Agent runtime 绑定、Team 绑定或远程部署；这些边界必须由各自后续发布独立验收。
