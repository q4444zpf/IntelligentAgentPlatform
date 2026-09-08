# Skill Foundation Verification

日期：2026-09-08。

最新进度：最终源码 `e500c0e` 的完整后端复核已通过：1286 通过、63 跳过、2 条既有警告，795.72 秒；真实 PostgreSQL/MinIO/迁移检查 10 项通过，依赖检查通过。整分支审查及集中修正后的定向复审已完成。源码已本地快进合入 main，提交树完全一致；main 目录受影响模块复核 286 项通过，未推送。此前一次 watchdog 耗时失败保留在下文，不能据后续通过宣称已消除所有计时敏感性。详见 [运行时修复计划](2026-09-08-runtime-tls-deadline.md)。

实施计划：[Skill Package And Version Foundation](2026-09-08-skill-package-version-foundation.md)。

## 范围与状态

分支 `codex/skill-version-foundation`，基线 `1251e20`。交付范围仅为包解析、不可变对象保存、作用域草稿与版本仓储及 PostgreSQL 迁移。未接入现有公开 Skill API、Agent/Team 运行时或界面；知识库与 Embedding 继续暂停。

实现提交：`893501a`、`c3aaa35`、`a6aa107`。三项任务分别通过独立审查。整批审查发现的默认 MinIO 配置遗漏、两个 ZIP 边界问题及两处测试覆盖缺口，已集中修复并提交为 `a33e49f`；独立复审逐项确认已解决，未发现新增 Critical/Important 问题，也没有待处置的审查观察项。

## 已有验证证据

| 验证 | 结果 | 说明 |
| --- | --- | --- |
| Python 3.12.10 独立环境 `pip check` | 通过 | 使用 backend/requirements.txt |
| 原有 Skill 和 Team 仓储基线 | 8 通过 | 一条既有 Starlette/AnyIO 警告 |
| 基础模块、旧 Skill/Team、迁移图聚焦回归 | 84 通过 | 修复前 a6aa107；一条既有警告 |
| 真实 PostgreSQL Skill 及迁移头检查 | 9 通过，0 跳过 | 独立测试数据库，包含锁竞争及回滚 |
| 真实 MinIO 文件包往返 | 1 通过，0 跳过 | 仅创建、回收本次 UUID 桶 |
| 完整后端回归 | 1231 通过，4 失败，63 跳过 | a6aa107，744.89 秒，2 条警告 |
| main 同环境对照复测上述四项 | 4 失败 | 1251e20，16.57 秒 |
| 最终修复后的聚焦回归 | 93 通过，0 跳过 | a33e49f，49.63 秒，一条既有警告 |
| 最终修复后的真实 PostgreSQL、MinIO、迁移头 | 10 通过，0 跳过 | a33e49f，7.29 秒，一条既有警告 |
| 单次 Workflow 生命周期专项 | 147 通过，0 跳过 | e2ae92b，58.29 秒，无警告；原四项两次独立复测及五次冷进程 Workflow 均通过 |
| 运行时修复后的完整后端回归 | 1284 通过，63 跳过 | e2ae92b，781.77 秒，2 条既有警告；尚不包含审查后的压缩异常修正 |
| 整分支审查修正后的文件包及运行时专项 | 191 通过，0 跳过 | e500c0e，51.09 秒；损坏 DEFLATE/LZMA 均有真实压缩数据 RED/GREEN 证据 |
| 最终代码第一次完整后端回归 | 1285 通过，1 失败，63 跳过 | e500c0e，844.47 秒，2 条既有警告；保留 watchdog 耗时失败，不以专项通过覆盖 |
| watchdog 所在完整测试文件复测 | 44 通过 | e500c0e，85.05 秒 |
| watchdog 独立进程对照 | 分支 1 通过，main 1 通过 | 顺序执行，pytest 分别耗时 2.39 / 2.60 秒，不是协调器内部耗时 |
| 最终代码真实 PostgreSQL、MinIO、迁移检查 | 10 通过，0 跳过 | e500c0e，6.04 秒，一条既有 Starlette/AnyIO 警告；pip check 同轮通过 |
| 最终代码有界完整复核 | 1286 通过，63 跳过 | e500c0e，795.72 秒，2 条既有警告；源码冻结，无并发测试负载，保留前次失败记录 |
| 合并后 main 目录受影响模块复核 | 286 通过，0 跳过 | e500c0e，167.39 秒，1 条既有警告；不是又一次完整后端回归 |

完整回归命令等价于 `python -m pytest backend/tests -q`，`DATABASE_URL` 仅指向专用测试库，清除 `TEST_DATABASE_URL` 和 `TEST_S3_*`，因此外部依赖测试的跳过不代表通过。真实 PostgreSQL 和 MinIO 另行运行，不混入完整回归的通过数。

两条完整回归警告为 Starlette/AnyIO 的 BlockingPortal 别名弃用及 Authlib 的 authlib.jose 弃用。没有通过过滤警告掩盖它们。

合并后使用 worktree 内 Python、main 目录下的 `backend` 作为 PYTHONPATH，检查全部 Skill 基础测试、TLS helper、Launcher、Gateway、Sandbox、Workflow、整个 run_lifecycle 文件、旧 Skill/Team 仓储及迁移图。验证前确认 main 与验收分支提交树没有差异；后续仅补充文档，不修改源码。

## 原四项运行时阻断的历史诊断

以下四项只在最终实际耗时断言失败，预期超时异常或 sandbox_timeout 结果已产生：

| 测试 | 本分支实测耗时 | main 实测耗时 | 断言上限 |
| --- | --- | --- | --- |
| test_launcher_client.py::test_launcher_prepare_enforces_execution_deadline_during_slow_response | 1.390 秒 | 1.281 秒 | 0.500 秒 |
| test_runner_gateway_client.py::test_execution_request_enforces_absolute_slow_drip_deadline | 0.797 秒 | 0.719 秒 | 0.450 秒 |
| test_sandbox_runtime.py::test_real_gateway_slow_drip_expiry_completes_as_sandbox_timeout | 1.297 秒 | 1.500 秒 | 0.600 秒 |
| test_workflow_runner.py::test_workflow_runner_submit_enforces_absolute_slow_drip_deadline | 0.718 秒 | 0.766 秒 | 0.500 秒 |

这些文件都位于 `backend/tests/runtime/`。`git diff 1251e20..a6aa107 -- backend/app/runtime backend/tests/runtime` 无输出，且同一虚拟环境运行未修改 main 复现全部失败，故不是 Skill 基础模块引入的回归。

原因证据：`httpx.AsyncClient(trust_env=False)` 的单次构造耗时 0.955 秒，其中 `ssl.SSLContext.load_verify_locations` 耗时 0.771 秒。现有运行时在 `asyncio.timeout` 内同步构造客户端；同步证书加载阻塞事件循环，超时取消不能及时执行。

诊断对照：仅在一个独立 Python 进程中，预先用 `ssl.create_default_context(cafile=certifi.where())` 构造真实、启用验证的上下文，并在 HTTPX 的默认验证、无客户端证书、trust_env=False 路径复用它；原测试不变，四项均通过（9.70 秒）。此运行出现一条 pytest 提前导入 anyio 的断言重写警告。没有禁用 TLS 验证，没有修改生产源码，诊断结果不计作正式回归通过。

上述原四项问题已由 `e2ae92b` 修复，经过冷进程、聚焦及完整回归验证；`e500c0e` 的完整回归中也未出现原四项失败。没有放宽原断言或关闭运行时证书校验。

## 最终回归的 watchdog 计时观察

`test_team_watchdog_expires_while_recovery_status_is_blocked` 一次完整回归测得 0.453 秒，超过严格小于 0.450 秒的断言。其 Runner 是本地阻塞替身，不经过本次 HTTP/TLS 代码。协调器、该测试、审计和会话仓储代码相对 main 未变；Skill 新增的全局 ORM 监听器仍可能间接影响开销，不能断言分支对计时完全没有影响。

该断言包括协调器创建、watchdog 启动前查询、150 毫秒状态等待及超时后的持久化、审计、令牌撤销和清理。单次总耗时超限不能确定 watchdog 本身触发晚了；失败当次也因该断言而没有执行后续终态断言。完整文件复测及顺序独立进程分支/main 对照均通过，包括 `failed` 终态断言。随后冻结代码的一次完整复核也通过。精确成因未能复现，保留计时敏感性风险；没有扩大生产修复、修改阈值或不断重跑直到通过。后续再次出现时应记录 watchdog 触发、持久化及清理的分段耗时。

## 测试资源与安全边界

PostgreSQL 16.14 专用测试库 `iap_skill_foundation_test_20260908_a`；业务库 `iap` 未修改。不可变测试行暂留专用库，整体回收时不得禁用业务触发器或清理业务数据。MinIO 测试使用 UUID 桶并自行回收。凭据仅从本机容器配置注入进程，不写入文档或 Git。

## 最终修复验证

修复前新增测试产生 6 项预期失败，分别复现文件祖先冲突、非法 UTF-8 文件名及默认工厂配置遗漏；两项既有行为的补充覆盖测试已通过。修复后针对性检查 9 项通过，包/存储/旧 Skill 合计 65 项通过。只格式化本次修复的五个文件，Ruff 检查和格式检查通过。

控制端在修复完成后独立运行以下测试，并非仅引用实现者报告：

```powershell
python -m pytest backend/tests/skills backend/tests/collaboration/test_repository.py backend/tests/test_skills.py backend/tests/integration/test_postgres_migrations.py::test_migration_graph_has_single_integration_head -q
python -m pytest backend/tests/integration/test_skill_versions_postgres.py backend/tests/integration/test_skill_package_minio.py backend/tests/integration/test_postgres_migrations.py::test_upgrade_head_creates_conversation_tables -q
```

第一条命令在 `a33e49f` 得到 93 项通过；第二条通过专用测试配置运行，得到 10 项通过。真实 MinIO 测试通过默认客户端工厂读取配置并完成往返，不再仅使用手工注入的客户端。后续运行时及最终压缩异常修复的完整回归见上表，聚焦结果不换算为完整通过。

## 提交与资源状态

源码和测试已分六次提交（四次 Skill 基础提交及两次运行时/审查修正）。最终完整回归和审查后，本地 main 从 1251e20 快进到 e500c0e，与验收源码完全一致；未推送。此前远端获取发生 TLS EOF，但后续显式开启 TLS 校验的 fetch 已成功，origin/main 为 242c91d，merge origin/main 返回 Already up to date。文档按先获取、合并、定向提交的顺序单独整理。

发现用户全局 Git 配置已有 `http.sslVerify=false`。本任务未修改持久配置；仅指定 sslBackend 的旧获取命令不能称为已验证 TLS。后续获取必须使用 `git -c http.sslVerify=true fetch origin`，不继承该不安全默认值，不关闭校验来绕过网络故障。文档和 Git 中不包含凭据。

最终源码完整回归及审查门禁已通过，单次 watchdog 计时失败作为残余风险记录。保留 worktree、其中的 Python 验证环境、专用测试库和本计划诊断文件；主目录没有独立虚拟环境，暂不清理该运行依赖。不回收其他任务资源，也不修改业务数据库。基础模块验收完成不等于整个 Skill 生产化阶段完成；公开 API、权限接入、版本运行时绑定和 UI 仍是后续任务。
