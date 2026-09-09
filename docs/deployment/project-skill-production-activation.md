# 项目 Skill 生产激活

本手册用于首次部署项目 Skill 生产接口。数据库迁移是显式运维操作，API 容器只启动 Uvicorn，不负责改变数据库结构或导入旧数据。

## 首次部署

1. 在部署环境配置 `DATABASE_URL`，并保持 `IAP_PROJECT_SKILLS_API_ENABLED=false`。确认 URL 指向目标数据库后再继续。
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
5. 迁移和验证成功后，将 `IAP_PROJECT_SKILLS_API_ENABLED=true` 注入 API 服务并启动或滚动更新服务。

普通 `docker compose up` 不会运行迁移。多实例部署中不得让任何 API 容器执行 Alembic；迁移只能由上述单次 `migrate` 操作完成，以免多个实例并发修改结构。

## 回滚

应用回滚时先将 `IAP_PROJECT_SKILLS_API_ENABLED=false`，再回滚 API 镜像或停止项目 Skill 流量。数据库迁移只向前处理，回滚绝不执行 `alembic downgrade`，也不得删除或重建数据库卷。若新版本无法继续使用，应保持功能关闭，保留数据库与迁移日志，并通过经审核的向前修复迁移恢复服务。
