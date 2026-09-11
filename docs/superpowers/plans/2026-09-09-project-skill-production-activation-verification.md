# Project Skill 生产激活验证

验证日期：2026-09-09；最终分支门禁补跑于 2026-09-10。范围是生产 `app.main.app`、真实 PostgreSQL/MinIO、API 镜像启动与 revision mismatch 发布演练。前端/浏览器、Agent runtime/Team 绑定和远程部署不在本次发布范围内。

## RED / GREEN

新增测试第一次真实 service-mode 运行退出 `1`，结果为 `2 errors in 16.43s`。真实 PostgreSQL 外键拒绝在 Unit 持久化前写入 Project；清理路径同时触发内置 Role 的 bulk-mutation 保护。测试夹具最小调整为先 flush UUID Unit/User，并在连接后二次确认专用库后对 UUID 自有数据执行精确 Core 清理。第二次需求相关 RED 为 `1 failed, 1 passed, 1 error in 17.20s`：根 `conftest` 先加载 settings，使 Origin 配置注入过晚，并再次证明内置 Role 不能通过 ORM 删除。Origin 配置移到 pytest 子进程启动前，清理改为嵌套 `finally`，保证数据库清理异常也不会跳过 UUID 桶回收。

最终 GREEN 命令由计划自有、git-ignored helper 在内存中读取现有测试容器凭据并启动新 pytest 进程；没有打印或写入凭据：

```powershell
python .superpowers/sdd/2026-09-09-project-skill-production-activation/task-6-service-mode.py
```

退出 `0`，`2 passed in 11.24s`，零 failed/skipped/warning。测试在 import `app.main` 前要求开关值为 `true`、双数据库 URL 和三项 S3 测试配置；开关不是 `true` 或任一配置缺失时执行普通 module skip。解析 URL 后确认两个 database 都精确为 `iap_project_skill_activation_test_20260909_a`，按 brief 仅对 `TEST_DATABASE_URL` 断言无 query，并在连接后用 `current_database()` 二次确认。它未 override `_service`、`get_session`、`require_request_context`、middleware 或 storage factory。

评审修复先用同一 helper 将其余配置保持完整、仅将开关显式设为 `false`，在 fresh pytest 进程复现 RED：

```powershell
$env:TASK6_FEATURE_FLAG = 'false'
python .superpowers/sdd/2026-09-09-project-skill-production-activation/task-6-service-mode.py
```

修复前 exit `1`，collection 在开关断言处失败，`1 error in 9.01s`。将 `true` 判断移入 import 前 module skip gate 后，用同一目标模块加一个默认关闭的已知通过测试避免 pytest 的 no-tests 退出状态：

```powershell
$env:TASK6_FEATURE_FLAG = 'false'
python .superpowers/sdd/2026-09-09-project-skill-production-activation/task-6-service-mode.py python -m pytest backend/tests/integration/test_project_skill_main_app.py backend/tests/test_main.py::test_project_skill_routes_are_absent_by_default -q -rs -o cache_dir=.pytest-task6-fix1-false
Remove-Item Env:TASK6_FEATURE_FLAG
```

exit `0`，`1 passed, 1 skipped in 6.10s`；skip 位于该真实 main-app 模块，且无 collection error。随后恢复 helper 默认 `true` 的真实 service mode，exit `0`，`2 passed in 12.75s`，零 skipped/warning。

测试使用 UUID unit/user/project/session/skill 和随机 Cookie token，token hash 为 SHA-256；通过 `seed_builtin_catalogue`、active unit/project memberships 与内置 `project_admin` project binding 构建身份。每次运行创建新的 `iap-project-skill-main-<uuid>` 桶，只枚举/删除该自有桶内对象并删除该桶。RED 清理诊断曾遗留两个同前缀自有桶和一组已知 UUID 行；专用清理命令在精确库双重确认后退出 `0`，报告 `LEAKED_ROWS_CLEANED=1 TASK_BUCKETS_CLEANED=2`。未枚举或修改共享桶。

## 生产边界

真实主应用证明完整 Project Skill method map 与旧 `/api/skills` 同时存在。无 Cookie GET 为 `401`；有效 Cookie GET 仅返回当前 fixture project；缺 CSRF 和 foreign Origin POST 都为 `403` 且 row/audit/object 不变；有效 POST 为 `201`，对象键位于 `unit/project/skill/`，并写入一条 `status=succeeded` 的 `skill.create` 审计；精确 surrogate bytes 返回 `422` 且三类计数不变。

API 镜像构建：

```powershell
docker compose build api
```

退出 `0`。Docker SDK helper 的实际调用命令为：

```powershell
python .superpowers/sdd/2026-09-09-project-skill-production-activation/task-6-image-rehearsal.py
```

helper 使用 `intelligent-agent-platform-api:local`、随机未占用 host port、宿主可达的专用服务数据库 URL 和 UUID Skill 桶启动容器；没有源码挂载，也没有业务库 URL。输出：

```text
HEALTH_STATUS=200 OLD_SKILL_ROUTE=True PROJECT_SKILL_ROUTE=True SOURCE_MOUNTS=0
MISMATCH_EXITED=True REVISION_MISMATCH=True SENTINEL_EXCLUDED=True
MIGRATION_DATABASE_RESTORED_TO_HEAD=True
```

helper 在 destructive 操作前解析无 query URL，并连接确认 `current_database()` 精确为 `iap_project_skill_activation_migration_20260909_a`。第二容器使用 revision 26，启动前退出且日志包含稳定 revision mismatch；日志不含随机 sentinel。`finally` 停止并删除仅有的具名容器，删除仅有的 UUID 镜像桶，并立即把迁移库升级回 head。首次 helper 诊断因 Docker 随机端口绑定形式和 Alembic `PYTHONPATH` 不完整退出 `1`；未执行 downgrade。后续脱敏日志定位宿主网关缺失，补充 `host-gateway` 后完整演练退出 `0`。

真实 Compose operations helper 对专用服务库和新的 `iap-project-skill-operations-<uuid>` 桶执行 runbook 命令；`LEGACY_SQLITE_DATA_DIR` 指向容器内空目录，未读取或改变共享旧数据。实际调用命令为：

```powershell
python .superpowers/sdd/2026-09-09-project-skill-production-activation/task-6-compose-operations.py
```

输出：

```text
MIGRATE_EXIT=0
SKILL_STORAGE_INIT_EXIT=0
DATABASE_AT_HEAD=True
UUID_BUCKET_HEAD=200
```

helper 命令 exit `0`。它对数据库 URL 先解析并拒绝 query，再用 `current_database()` 确认精确库名；子进程输出经凭据 sentinel 检查。结束后只删除该空 UUID 桶。

## 兼容与真实服务

所有组均顺序运行，没有并发执行 timing-sensitive suites。以下为实际 PowerShell 命令和选择范围；每条 pytest 命令均使用 `-q -rs -o cache_dir=.pytest-task6`：

```powershell
$task6ProjectAll = @(Get-ChildItem backend/tests/skills/test_project*.py | ForEach-Object FullName)
python -m pytest @task6ProjectAll -q -rs -o cache_dir=.pytest-task6

$task6ProjectNoCookie = @($task6ProjectAll | Where-Object { $_ -notlike '*test_project_cookie_api.py' })
python -m pytest @task6ProjectNoCookie -q -rs -o cache_dir=.pytest-task6
$env:TASK6_DATABASE_NAME = 'iap_skill_control_test_20260908_a'
python .superpowers/sdd/2026-09-09-project-skill-production-activation/task-6-service-mode.py python -m pytest backend/tests/skills/test_project_cookie_api.py -q -rs -o cache_dir=.pytest-task6
Remove-Item Env:TASK6_DATABASE_NAME

python -m pytest backend/tests/test_skills.py -q -rs -o cache_dir=.pytest-task6
python -m pytest backend/tests/identity -q -rs -o cache_dir=.pytest-task6
python -m pytest backend/tests/audit -q -rs -o cache_dir=.pytest-task6
python -m pytest backend/tests/test_agents.py -q -rs -o cache_dir=.pytest-task6
python -m pytest backend/tests/collaboration -q -rs -o cache_dir=.pytest-task6
python -m pytest backend/tests/runtime -q -rs -o cache_dir=.pytest-task6
```

| 选择范围 | exit | passed / failed / skipped / warning |
| --- | ---: | --- |
| `skills/test_project*.py` 初次运行 | 1 | 239 / 47 / 0 / 0；47 项均为 Cookie 文件专用库 guard |
| 同组排除 Cookie 文件 | 0 | 226 / 0 / 0 / 0 |
| Cookie 文件，确认的 `iap_skill_control_test_20260908_a` | 0 | 60 / 0 / 0 / 0 |
| 旧 `tests/test_skills.py` | 0 | 5 / 0 / 0 / 0 |
| `tests/identity` | 0 | 135 / 0 / 1 / 0 |
| `tests/audit` | 0 | 107 / 0 / 0 / 0 |
| `tests/test_agents.py` | 0 | 67 / 0 / 2 / 0 |
| `tests/collaboration` | 0 | 38 / 0 / 0 / 0 |
| `tests/runtime` | 1 | 508 / 3 / 0 / 0 |

Project Skill 等价最终结果为 `286 passed`、零 failed/skipped/warning。Identity 的 skip 是 Windows 不适用的 POSIX ownership/symlink contract；Agent 两项 skip 是 Windows 无法创建所需文件或目录 symlink。

真实服务命令如下。service-mode helper 在内存中映射现有容器凭据，命令和报告均不输出凭据：

```powershell
python .superpowers/sdd/2026-09-09-project-skill-production-activation/task-6-service-mode.py

$env:TASK6_DATABASE_NAME = 'iap_skill_control_test_20260908_a'
python .superpowers/sdd/2026-09-09-project-skill-production-activation/task-6-service-mode.py python -m pytest backend/tests/integration/test_project_skill_storage_bootstrap_minio.py backend/tests/integration/test_skill_package_minio.py backend/tests/integration/test_skill_control_plane_minio.py -q -rs -o cache_dir=.pytest-task6
python .superpowers/sdd/2026-09-09-project-skill-production-activation/task-6-service-mode.py python -m pytest backend/tests/integration/test_skill_control_plane_postgres.py -q -rs -o cache_dir=.pytest-task6
python .superpowers/sdd/2026-09-09-project-skill-production-activation/task-6-service-mode.py python -m pytest backend/tests/integration/test_skill_versions_postgres.py -q -rs -o cache_dir=.pytest-task6
Remove-Item Env:TASK6_DATABASE_NAME

& .superpowers/sdd/2026-09-09-project-skill-production-activation/run_migration_test_mode.ps1 -PytestArguments @('backend/tests/integration/test_project_skill_startup_postgres.py', '-q', '-rs', '-p', 'no:cacheprovider', '--tb=short')
```

| 选择范围 | exit | passed / failed / skipped / warning |
| --- | ---: | --- |
| real main app PostgreSQL/MinIO | 0 | 2 / 0 / 0 / 0 |
| 三个 real MinIO 文件 | 0 | 16 / 0 / 0 / 0 |
| real control-plane PostgreSQL | 0 | 20 / 0 / 0 / 0 |
| real version PostgreSQL | 0 | 8 / 0 / 0 / 0 |
| real startup/migration PostgreSQL | 0 | 1 / 0 / 0 / 0 |

startup/migration 首次运行 exit `1`：helper 未把 `backend` 加入测试内嵌 Alembic 子进程的 `PYTHONPATH`。fixture 已只重置精确迁移库；修正 helper 后立即将该库升级到 `20260908_27 (head)`，再次运行 exit `0`、`1 passed in 14.37s`。真实服务选择集零配置 skip。

fetch/merge 后再次执行受影响门禁：

```powershell
python .superpowers/sdd/2026-09-09-project-skill-production-activation/task-6-service-mode.py
$env:TASK6_DATABASE_NAME = 'iap_skill_control_test_20260908_a'
python .superpowers/sdd/2026-09-09-project-skill-production-activation/task-6-service-mode.py python -m pytest backend/tests/skills/test_project_cookie_api.py -q -rs -o cache_dir=.pytest-task6
python -m pytest backend/tests/test_project_skill_deployment.py backend/tests/test_main.py -q -rs -o cache_dir=.pytest-task6
python .superpowers/sdd/2026-09-09-project-skill-production-activation/task-6-service-mode.py python -m pytest backend/tests/integration/test_project_skill_storage_bootstrap_minio.py backend/tests/integration/test_skill_package_minio.py backend/tests/integration/test_skill_control_plane_minio.py backend/tests/integration/test_skill_control_plane_postgres.py backend/tests/integration/test_skill_versions_postgres.py -q -rs -o cache_dir=.pytest-task6
Remove-Item Env:TASK6_DATABASE_NAME
docker compose build api
python .superpowers/sdd/2026-09-09-project-skill-production-activation/task-6-image-rehearsal.py
```

结果依次为：main app exit `0`、`2 passed in 18.78s`；Cookie exit `0`、`60 passed in 158.91s`；deployment/main exit `0`、`14 passed in 55.69s`；combined real PostgreSQL/MinIO exit `0`、`44 passed in 27.29s`；镜像 build exit `0`；镜像演练 exit `0` 且再次得到上文全部布尔门禁。以上四组 pytest 均为零 failed/skipped/warning。

Runtime 兼容门禁明确为**未通过**：三项失败均来自 `test_real_worker_process_does_not_join_abandoned_team_deadline_work` 的 planning/member/synthesis 参数，错误为 worker 未进入 selected slow stage。planning node 单独重跑 exit `1`，`1 failed in 51.59s`。只读核对 `backend/tests/runtime/test_sandbox_runtime.py` 对应流程，子进程在写 ready 文件前执行 `runpy.run_path('tests/runtime/test_sandbox_runtime.py')`；独立测量两次分别耗时 `42.500s` 和 `44.016s`，超过固定 10 秒 ready budget。Task 6 未修改 runtime、该测试或任何生产 Python，现有证据不支持将此残留归因于 Task 6 接线；它作为非本任务阻断的兼容残留留给最终发布裁决，本文不声称全兼容门禁通过。

## 发布与回滚

生产步骤及退出码门禁见[项目 Skill 生产激活手册](../../deployment/project-skill-production-activation.md)：build API 镜像；单实例显式运行 `migrate`；比对数据库 current 与镜像唯一 head；检查 importer 实际处理的 model providers、Agents 和 MCP clients（不包含 Skill）；运行 `skill-storage-init`；开启 feature flag；执行 health/OpenAPI/401 与真实 Cookie/CSRF smoke。API 启动不迁移、不建桶，MinIO 不成为全局启动门槛。

回滚将 `IAP_PROJECT_SKILLS_API_ENABLED=false` 后必须重新创建或重启全部 API 实例，再确认 OpenAPI 不含 `/api/project-skills` 且旧 `/api/skills` 仍可用；移除路由不要求额外回滚镜像。回滚不 downgrade、不删除数据库/对象/桶。本次没有 push 或远程部署。

## 最终分支门禁（2026-09-10）

最终安全 fetch 使用临时 TLS/HTTP 代理参数，exit `0`；`git merge --no-edit origin/main` exit `0` 并返回 `Already up to date`。没有修改全局 Git 配置。

清除全部外部 `TEST_*`，仅把 `DATABASE_URL` 指向专用 `iap_skill_control_test_20260908_a` 后运行完整后端：

```powershell
python -m pytest backend/tests -q -p no:cacheprovider --tb=short
```

exit `1`，`1618 passed, 103 skipped, 3 failed in 1210.94s`。三个失败精确为 `test_real_worker_process_does_not_join_abandoned_team_deadline_work` 的 planning/member/synthesis 参数，与第 154 行记录的 Windows module preload 和固定 ready budget 不匹配一致；无其他失败。因此本文明确不声明完整后端回归通过。

真实服务重新运行：真实生产主应用 `2 passed in 13.35s`；三个 MinIO 文件、control-plane PostgreSQL 和 version PostgreSQL 合并选择 `44 passed in 17.94s`；专用 migration PostgreSQL 启动选择 `1 passed in 7.49s`。三组 exit 均为 `0`，零配置 skip。首次通过 `powershell -File` 传递 pytest 位置参数在参数绑定阶段 exit `1`，未启动 pytest 或连接数据库；改用上文显式 `-PytestArguments` 数组后通过。

静态和发布边界结果：

- 计划复用的项目虚拟环境运行 `python -m pip check` exit `0`，输出 `No broken requirements found.`；工作站全局 Python 的 `pip check` 仍有与本分支无关的预装水利/AI 包依赖冲突。
- 全仓 `python -m ruff check backend` exit `1`，报告 575 个既有 lint 问题。分支变更 Python 文件的普通 Ruff exit `1`，仅剩 4 个 `I001`，对应在计划基线已不满足 import sorting 的 `settings.py`、`main.py`、`test_settings.py`、`test_main.py`；对这些已记录基线项使用 `--ignore I001` 后 exit `0`。
- 分支变更 Python 文件的 Black check exit `1`：既有文件已在 `4930f67` 基线上失败。最终修复波次已经评审封闭，未追加未评审格式化改动，因此本文不声明 Black 门禁通过。
- `git diff --check 4930f67 HEAD`、普通 `docker compose config --quiet`、operations profile Compose config、API 镜像构建均 exit `0`。
- 镜像实际 `Config.Cmd` 为 `["uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--proxy-headers"]`。
- 从计划基线比较，旧 Skill router/service、frontend、agents、collaboration 和 runtime 均无 diff；`backend/app/main.py` 是唯一应用 composition 变更。

最终全分支评审及唯一 consolidated fix wave 的 scoped re-review 均没有遗留 Critical/Important。由于完整后端和 Black 门禁仍非绿色，分支保留在隔离 worktree，不执行合并、推送、远程部署或 SDD scratch 清理。

## Runtime Probe Remediation Final Gates (2026-09-11)

本节是 `2026-09-10-runtime-probe-test-remediation` 的 Task 3 持久证据。所有 timing-sensitive pytest 选择集均顺序运行。每次完整后端运行前，外部 `TEST_*` 均被清除；仅在该测试进程内以现有测试容器凭据安全构造 `DATABASE_URL`，目标精确为获批 Cookie 专用库 `iap_skill_control_test_20260908_a`。命令、临时捕获和本文均未打印或持久化凭据。

### Runtime and complete backend

```powershell
python -m pytest backend/tests/runtime -q -rs -p no:cacheprovider --tb=short
python -m pytest backend/tests -q -rs -p no:cacheprovider --tb=short
```

运行时选择集 exit `1`，`510 passed, 1 failed, 0 skipped, 0 warnings in 336.93s`；完整后端初次有效捕获 exit `1`，`1620 passed, 103 skipped, 1 failed, 0 warnings in 1242.87s`。两者唯一失败均为 `backend/tests/runtime/test_run_lifecycle.py::test_team_watchdog_clamps_poll_sleep_to_remaining_deadline`：`max(sleep_calls)` 为 `0.1500000000014552`，而断言要求 `<= 0.15`。

依照 systematic-debugging，focused 重现命令 exit `1`，`1 failed in 6.32s`；`4930f67..HEAD` 未改变该测试或 `backend/app/runtime/run_lifecycle.py`。现有测试以真实 `time.monotonic()` 计算二进制浮点剩余时间并对非精确十进制上界作精确比较，因此失败是可重现的既有基线测试精度债务，非 worker deadline probe、生产 runtime 或 timeout 改动。没有放宽 timeout、改生产代码或修复无关基线。

### Real PostgreSQL and MinIO

以下 credential-safe helper 只在进程内读取既有测试容器凭据；未 reset/downgrade `iap`，且 MinIO 测试只操作自有 UUID 桶：

```powershell
python .superpowers/sdd/2026-09-09-project-skill-production-activation/task-6-service-mode.py

$env:TASK6_DATABASE_NAME = 'iap_skill_control_test_20260908_a'
python .superpowers/sdd/2026-09-09-project-skill-production-activation/task-6-service-mode.py python -m pytest backend/tests/integration/test_project_skill_storage_bootstrap_minio.py backend/tests/integration/test_skill_package_minio.py backend/tests/integration/test_skill_control_plane_minio.py backend/tests/integration/test_skill_control_plane_postgres.py backend/tests/integration/test_skill_versions_postgres.py -q -rs -p no:cacheprovider --tb=short
Remove-Item Env:TASK6_DATABASE_NAME

& .superpowers/sdd/2026-09-09-project-skill-production-activation/run_migration_test_mode.ps1 -PytestArguments @('backend/tests/integration/test_project_skill_startup_postgres.py', '-q', '-rs', '-p', 'no:cacheprovider', '--tb=short')
```

结果依次为 exit `0`、`2 passed in 13.57s`；exit `0`、`44 passed in 21.25s`；exit `0`、`1 passed in 14.08s`。三组均为零 failed、零 skipped、零 warnings，因而没有 configuration skip。

### Static and release boundaries

| Command | exit | Result |
| --- | ---: | --- |
| `..\skill-version-foundation\.venv\Scripts\python.exe -m pip check` | 0 | `No broken requirements found.` |
| `python -m ruff check --ignore I001 @changedPython` (`4930f67..HEAD`) | 0 | branch-owned, baseline-aware Ruff passed |
| `python -m black --check @addedPython` (`4930f67..HEAD`, added files) | 0 | added-file Black passed |
| `git diff --check 4930f67 HEAD` | 0 | no whitespace errors |
| `docker compose config --quiet` | 0 | passed |
| `docker compose --profile operations config --quiet` | 0 | passed |
| `docker compose build api` | 0 | API image built |
| protected-path `git diff --exit-code 4930f67 HEAD -- backend/app/skills/router.py backend/app/skills/service.py frontend backend/app/agents backend/app/collaboration backend/app/runtime` | 0 | protected paths unchanged |

镜像检查 `docker image inspect --format '{{json .Config.Cmd}}' intelligent-agent-platform-api:local` exit `0`，精确输出 `["uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--proxy-headers"]`。

普通基线记录没有被掩盖：`python -m ruff check backend` exit `1`，`575` 个既有问题；普通分支变更文件 `python -m black --check @changedPython` exit `1`，仅报告既有的 `backend/app/core/settings.py`、`backend/app/main.py`、`backend/tests/core/test_settings.py`、`backend/tests/test_main.py` 和 `backend/tests/runtime/test_sandbox_runtime.py`。不再存在 `project_startup.py` 的 Black 差异。

### Fetch, merge, and post-fetch rerun

```powershell
git -c http.sslVerify=true -c http.sslBackend=openssl -c http.proxy=http://127.0.0.1:7897 -c https.proxy=http://127.0.0.1:7897 -c http.version=HTTP/1.1 -c http.sslVersion=tlsv1.2 fetch origin
git merge --no-edit origin/main
python -m pytest "backend/tests/runtime/test_sandbox_runtime.py::test_real_worker_process_does_not_join_abandoned_team_deadline_work" -q -p no:cacheprovider --tb=short
```

secure fetch exit `0`；merge exit `0`、`Already up to date`，未改变源代码。post-fetch 三参数 deadline test exit `0`、`3 passed, 0 skipped, 0 warnings in 57.73s`。post-fetch 完整后端再次在相同受控环境运行，exit `1`、`1620 passed, 103 skipped, 1 failed, 0 warnings in 1228.03s`；唯一失败未变化。post-fetch added-file Black、branch Ruff (`--ignore I001`)、`git diff --check` 和 protected-path diff 均 exit `0`。

### Final status

The historical blocked results above are retained. A verified Task 3 fix then replaced only the wall-clock-coupled watchdog test with a test-local controllable monotonic clock and clock-advancing sleeper, retaining `poll_interval=0.6` and `timeout_seconds=0.15`. Focused GREEN was `1 passed in 3.40s`; a temporary production mutation to an unclamped poll sleep produced the expected RED, `[0.6] != [0.15]`, then production was immediately restored and `backend/app/runtime` diff was clean. Restored focused GREEN was `1 passed in 2.98s`.

Fresh gates after that test-only correction were green: runtime exit `0`, `511 passed, 0 skipped, 0 warnings in 244.47s`; complete backend exit `0`, `1621 passed, 103 skipped, 0 warnings in 1218.30s`. Secure fetch exit `0` and merge returned `Already up to date`; post-fetch three-parameter deadline test exit `0`, `3 passed, 0 skipped, 0 warnings in 107.31s`; post-fetch complete backend exit `0`, `1621 passed, 103 skipped, 0 warnings in 1451.69s`. Post-fetch added-file Black, branch Ruff (`--ignore I001`), diff check, and protected-path check all exited `0`.

The earlier review reference to orphan commit `a41b9ce` is not a whole-remediation final review conclusion and is withdrawn. This section records only Task 3's independently reviewed deterministic test correction; the controller will generate the final-HEAD whole-remediation review package separately.
