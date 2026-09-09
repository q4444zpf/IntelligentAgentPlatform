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
4. 验证数据库的 `alembic_version` 是当前镜像的 Alembic head，并检查旧 SQLite Skill 数据导入结果。不得用 API 容器启动成功代替这项验证。
5. 使用本次 API 镜像显式初始化 Skill 桶：

   ```powershell
   docker compose --profile operations run --rm skill-storage-init
   if ($LASTEXITCODE -ne 0) { throw "Project Skill storage initialization failed" }
   ```

   作业先检查桶；仅缺桶时创建，并在创建后再次检查。已有可访问桶和同账户并发创建可重复执行；其他账户占用、权限不足、连接错误或创建后检查失败均返回非零退出码，并只输出稳定错误行 `unable to initialize project skill bucket`。默认区域不发送 `LocationConstraint`，其他区域使用配置值。失败时保持功能关闭，检查目标 endpoint、区域、桶归属和授权后重试。禁止为了重试删除已有桶或其中对象。
6. 数据库迁移、桶初始化和验证均成功后，将 `IAP_PROJECT_SKILLS_API_ENABLED=true` 注入 API 服务并启动或滚动更新服务。

普通 `docker compose up` 不会运行迁移。多实例部署中不得让任何 API 容器执行 Alembic；迁移只能由上述单次 `migrate` 操作完成，以免多个实例并发修改结构。

`skill-storage-init` 同样只属于 `operations` profile，使用与 API 相同的 endpoint、访问凭据、区域和桶配置。API 不依赖任何运维作业，启动成功不能代替初始化作业退出码验证。

## 回滚

应用回滚时先将 `IAP_PROJECT_SKILLS_API_ENABLED=false`，再回滚 API 镜像或停止项目 Skill 流量。数据库迁移只向前处理，回滚绝不执行 `alembic downgrade`，也不得删除或重建数据库卷。若新版本无法继续使用，应保持功能关闭，保留数据库与迁移日志，并通过经审核的向前修复迁移恢复服务。
