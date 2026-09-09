# Project Skill 生产激活验证

验证日期：2026-09-09。范围是生产 `app.main.app`、真实 PostgreSQL/MinIO、API 镜像启动与 revision mismatch 发布演练。前端/浏览器、Agent runtime/Team 绑定和远程部署不在本次发布范围内。

## RED / GREEN

新增测试第一次真实 service-mode 运行退出 `1`，结果为 `2 errors in 16.43s`。真实 PostgreSQL 外键拒绝在 Unit 持久化前写入 Project；清理路径同时触发内置 Role 的 bulk-mutation 保护。测试夹具最小调整为先 flush UUID Unit/User，并在连接后二次确认专用库后对 UUID 自有数据执行精确 Core 清理。第二次需求相关 RED 为 `1 failed, 1 passed, 1 error in 17.20s`：根 `conftest` 先加载 settings，使 Origin 配置注入过晚，并再次证明内置 Role 不能通过 ORM 删除。Origin 配置移到 pytest 子进程启动前，清理改为嵌套 `finally`，保证数据库清理异常也不会跳过 UUID 桶回收。

最终 GREEN 命令由计划自有、git-ignored helper 在内存中读取现有测试容器凭据并启动新 pytest 进程；没有打印或写入凭据：

```powershell
python .superpowers/sdd/2026-09-09-project-skill-production-activation/task-6-service-mode.py
```

退出 `0`，`2 passed in 11.24s`，零 failed/skipped/warning。测试在 import `app.main` 前要求开关、双数据库 URL 和三项 S3 测试配置，解析 URL 后确认两个 database 都精确为 `iap_project_skill_activation_test_20260909_a` 且无 query，并在连接后用 `current_database()` 二次确认。它未 override `_service`、`get_session`、`require_request_context`、middleware 或 storage factory。

测试使用 UUID unit/user/project/session/skill 和随机 Cookie token，token hash 为 SHA-256；通过 `seed_builtin_catalogue`、active unit/project memberships 与内置 `project_admin` project binding 构建身份。每次运行创建新的 `iap-project-skill-main-<uuid>` 桶，只枚举/删除该自有桶内对象并删除该桶。RED 清理诊断曾遗留两个同前缀自有桶和一组已知 UUID 行；专用清理命令在精确库双重确认后退出 `0`，报告 `LEAKED_ROWS_CLEANED=1 TASK_BUCKETS_CLEANED=2`。未枚举或修改共享桶。

## 生产边界

真实主应用证明完整 Project Skill method map 与旧 `/api/skills` 同时存在。无 Cookie GET 为 `401`；有效 Cookie GET 仅返回当前 fixture project；缺 CSRF 和 foreign Origin POST 都为 `403` 且 row/audit/object 不变；有效 POST 为 `201`，对象键位于 `unit/project/skill/`，并写入一条 `status=succeeded` 的 `skill.create` 审计；精确 surrogate bytes 返回 `422` 且三类计数不变。

API 镜像构建：

```powershell
docker compose build api
```

退出 `0`。Docker SDK helper 使用 `intelligent-agent-platform-api:local`、随机未占用 host port、宿主可达的专用服务数据库 URL 和 UUID Skill 桶启动容器；没有源码挂载，也没有业务库 URL。输出：

```text
HEALTH_STATUS=200 OLD_SKILL_ROUTE=True PROJECT_SKILL_ROUTE=True SOURCE_MOUNTS=0
MISMATCH_EXITED=True REVISION_MISMATCH=True SENTINEL_EXCLUDED=True
MIGRATION_DATABASE_RESTORED_TO_HEAD=True
```

helper 在 destructive 操作前解析无 query URL，并连接确认 `current_database()` 精确为 `iap_project_skill_activation_migration_20260909_a`。第二容器使用 revision 26，启动前退出且日志包含稳定 revision mismatch；日志不含随机 sentinel。`finally` 停止并删除仅有的具名容器，删除仅有的 UUID 镜像桶，并立即把迁移库升级回 head。首次 helper 诊断因 Docker 随机端口绑定形式和 Alembic `PYTHONPATH` 不完整退出 `1`；未执行 downgrade。后续脱敏日志定位宿主网关缺失，补充 `host-gateway` 后完整演练退出 `0`。

真实 Compose operations helper 对专用服务库和新的 `iap-project-skill-operations-<uuid>` 桶执行 runbook 命令；`LEGACY_SQLITE_DATA_DIR` 指向容器内空目录，未读取或改变共享旧数据。输出：

```text
MIGRATE_EXIT=0
SKILL_STORAGE_INIT_EXIT=0
DATABASE_AT_HEAD=True
UUID_BUCKET_HEAD=200
```

helper 对数据库 URL 先解析并拒绝 query，再用 `current_database()` 确认精确库名；子进程输出经凭据 sentinel 检查。结束后只删除该空 UUID 桶。

## 兼容与真实服务

所有组均顺序运行，没有并发执行 timing-sensitive suites：

| 组 | 结果 | skip / warning |
| --- | --- | --- |
| Project Skill isolated API/Cookie | 286 passed | 0 / 0 |
| 旧 `/api/skills` | 5 passed | 0 / 0 |
| identity/session | 135 passed | 1 / 0 |
| audit | 107 passed | 0 / 0 |
| Agent | 67 passed | 2 / 0 |
| collaboration | 38 passed | 0 / 0 |
| runtime | 508 passed, 3 failed | 0 / 0 |
| real main app PostgreSQL/MinIO | 2 passed | 0 / 0 |
| real MinIO selections | 16 passed | 0 / 0 |
| real control-plane PostgreSQL | 20 passed | 0 / 0 |
| real version PostgreSQL | 8 passed | 0 / 0 |
| real startup/migration PostgreSQL | 1 passed | 0 / 0 |

Identity 的 skip 是 Windows 不适用的 POSIX ownership/symlink contract；Agent 两项 skip 是 Windows 无法创建所需文件或目录 symlink。真实服务选择集零配置 skip。

Runtime 三项失败均来自 `test_real_worker_process_does_not_join_abandoned_team_deadline_work` 的 planning/member/synthesis 参数，错误为 worker 未进入 selected slow stage。planning node 单独重跑仍以 `1 failed in 51.59s` 失败。Phase 1 诊断显示子进程在写 ready 文件前执行 `runpy.run_path('tests/runtime/test_sandbox_runtime.py')`，独立测量两次分别耗时 `42.500s` 和 `44.016s`，超过测试固定的 10 秒 ready budget。Task 6 未修改越界的 runtime 或测试代码；该稳定 Windows timing-gate finding 留给最终发布裁决。

## 发布与回滚

生产步骤及退出码门禁见[项目 Skill 生产激活手册](../../deployment/project-skill-production-activation.md)：build API 镜像；单实例显式运行 `migrate`；比对数据库 current 与镜像唯一 head；检查 importer 实际处理的 model providers、Agents 和 MCP clients（不包含 Skill）；运行 `skill-storage-init`；开启 feature flag；执行 health/OpenAPI/401 与真实 Cookie/CSRF smoke。API 启动不迁移、不建桶，MinIO 不成为全局启动门槛。

回滚只将 `IAP_PROJECT_SKILLS_API_ENABLED=false`，不 downgrade、不删除数据库/对象/桶；旧 `/api/skills` 保持可用。本次没有 push 或远程部署。
