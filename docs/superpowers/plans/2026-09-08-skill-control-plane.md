# Skill Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付独立测试的项目 Skill 读取、草稿、导入和发布 API，具备服务端授权、不可变版本及同事务审计。

**Architecture:** 复用已经验收的包解析、MinIO 对象适配器和 PostgreSQL 草稿/版本仓储。新增独立路由、应用服务及窄范围权限和包操作模块；应用服务使用独立会话工厂拥有每个事务，不提交身份依赖使用的会话。新路由只在测试应用显式挂载，不接管旧目录或主应用入口。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、SQLAlchemy 2、Alembic、PostgreSQL、Boto3/MinIO、pytest；不新增依赖。

## Global Constraints

- 依据已确认设计：`docs/superpowers/specs/2026-09-08-skill-control-plane-design.md`；源码基线 `51c1aad`，设计提交 `316f511`。
- 接口前缀为 `/api/project-skills`；本轮不在 `app.main` 导入或挂载新路由，不增加生产配置开关。
- 旧 `/api/skills`、SkillService、前端、Agent/Team、历史快照和本地目录不变；新服务不得读取旧目录或进行双写。
- 不做停用/删除、附件下载、引用查询、回退发布、版本绑定、菜单改版、知识库、Embedding、脚本执行、依赖安装、业务数据迁移、推送和部署。
- 权限顺序：认证及当前项目检查 → 本次操作入口能力 → 本次操作数据范围内资源。401 / 403 / 404 严格区分；own 过滤必须作用于查询和 total，不是分页后过滤。
- ZIP 沿用 10 MiB 压缩包、20 MiB 实际解压内容和 500 条目上限；JSON 文本输入最多 200,000 字符；manifest 最多 500 项、UTF-8 编码最多 256 KiB。
- offset 默认 0；limit 默认 20 且 1–100；q 最长 120 字符，按 name 字面包含匹配；资源 created_at/id 升序，版本 version 降序。
- revision 为严格正整数；Idempotency-Key 为 1–128 个可打印 ASCII 字符，不修剪或改写。
- 不接收客户端指定的 unit_id、project_id、created_by、object_key 或可信摘要；返回显式 Schema，不泄露存储位置和凭据。
- 对象先准备、短写事务后提交；不在 MinIO I/O 期间持有行锁。数据库变更及成功审计原子提交；未引用对象允许暂留，不自动删除任何桶或共享数据。
- 每项按行为 RED → 最小实现 → GREEN → 定向审查/提交。遇到原运行时计时失败先诊断，不放宽断言，不并发执行计时套件。
- 保留用户主目录文件及其他工作区。继续在现有 `.worktrees/skill-version-foundation` 内的 `codex/skill-control-plane` 分支操作；目录名保留是为复用 Python 环境，不表示仍在修改旧分支。
- Git 获取显式启用 TLS：`git -c http.sslVerify=true -c http.sslBackend=openssl fetch origin`。每次提交前获取、合并 origin/main、验证，再定向提交；不修改用户全局 Git 配置。

---

## 状态与顺序

- [x] 书面设计已获用户确认。
- [x] 核对实际源码、权限目录、审计接口、迁移 head 与测试辅助 API。
- [x] 计划自检完成，纳入本次文档定向提交。
- [x] 执行方式选择：同会话子代理顺序实施和审查。
- [x] Task 1：项目授权和增量权限迁移。
- [x] Task 2：作用域查询、响应契约及只读 HTTP API。
- [x] Task 3：有界包处理与草稿创建/保存。
- [x] Task 4：原子批量导入。
- [x] Task 5：对象复验、幂等发布及同事务审计。
- [x] 全分支审查、真实集成、完整回归及交付记录。

本阶段隔离 API 验收完成；最终源码 `aa06783` 完整后端回归 1556 passed / 100 skipped。证据、首次失败及重跑、TDD 历史偏差和未处理限制见[验收报告](2026-09-08-skill-control-plane-verification.md)。下文细分步骤保留原计划，不将有历史限定的 RED 步骤补记为无偏差执行。尚未挂载生产路由、切换前端、合并 main、推送或部署。

Tasks 1–5 顺序执行，避免同时编辑 project_service、project_router 和 repository。最终审查及全量验收由控制端统一执行，不为每个小改动重复全量套件。

## 环境与验证入口

运行目录为上述 worktree。所有命令显式使用 `.venv/Scripts/python.exe`；每个测试进程设置 `PYTHONPATH=backend`。默认快测清除 TEST_DATABASE_URL / TEST_S3_ENDPOINT / TEST_S3_ACCESS_KEY / TEST_S3_SECRET_KEY，DATABASE_URL 只指向本子计划测试库，不指向业务库。

执行 Task 1 时，在计划专属忽略目录 `.superpowers/sdd/2026-09-08-skill-control-plane/` 创建验证辅助脚本。参考上阶段 run-validation.ps1 的进程注入和 finally 恢复方式，但禁止照搬其数据库名。使用 `iap_skill_control_test_20260908_a` 做本轮服务集成；另用 `iap_skill_control_migration_20260908_a` 做 26→27 迁移前后对照。创建前只读检查重名库；已有非本任务库时换唯一后缀并记录，不覆盖。

测试资源尚未创建。先核实容器健康和既有 Python 环境，再仅创建这两个专用库。权限迁移只在第二个库执行降级/升级试验；业务库 `iap` 和上阶段测试库不变。真实 MinIO 使用每次 UUID 桶且仅清理测试自行创建的对象。

```powershell
& '.venv/Scripts/python.exe' -m pip check
$env:PYTHONPATH = 'backend'
& '.venv/Scripts/python.exe' -m pytest backend/tests/skills/test_repository.py backend/tests/test_skills.py backend/tests/identity/test_authorization.py -q -p no:cacheprovider
& '.venv/Scripts/python.exe' -m alembic -c backend/alembic.ini heads
```

这些是待执行命令，不是新接口通过证据。计划编写期间不创建数据库、不执行迁移。

## 文件归属与接口约定

| 文件 | 责任及首次归属 |
| --- | --- |
| `backend/app/skills/project_access.py` | Task 1；认证上下文一致性、项目有效性及查询所需所有者范围 |
| `backend/app/skills/project_errors.py` | Task 1；新 API 预期错误的 code/status 契约，不改变旧异常 |
| `backend/app/identity/catalogue.py` | Task 1；只补 project_admin 的 skill.manage 默认项目授权 |
| `backend/alembic/versions/20260908_27_project_skill_permissions.py` | Task 1；对已有内置项目管理员追加缺失授权 |
| `backend/app/skills/repository.py` | Task 2 查询扩展；Tasks 3/4 公开作用域锁，Task 5 公开幂等查询和复用请求摘要 |
| `backend/app/skills/project_schemas.py` | Task 2 读取 DTO；Task 3 写请求；Task 4 导入 discriminated union；Task 5 发布请求 |
| `backend/app/skills/project_service.py` | Task 2 起；请求级编排、拥有独立事务和审计；按能力增量补齐，禁止实例级共享 Session |
| `backend/app/skills/project_router.py` | Task 2 起；新路由及依赖，任务增量注册各自方法 |
| `backend/app/skills/project_packages.py` | Task 3；有界快照、正文替换、对象复验；Task 4 补结构化清单改名 |
| `backend/tests/skills/project_support.py` | Task 1 起；本批测试 fixture 与真实内存 S3 边界，不自动覆盖旧 conftest |
| `backend/tests/skills/test_project_access.py` | Task 1 授权行为 |
| `backend/tests/skills/test_project_reads.py`、`test_project_api.py` | Task 2 起，测试每批新增公开行为 |
| `backend/tests/skills/test_project_drafts.py`、`test_project_packages.py` | Task 3 |
| `backend/tests/skills/test_project_import.py` | Task 4 |
| `backend/tests/skills/test_project_publish.py` | Task 5 |
| `backend/tests/skills/test_project_cookie_api.py` | Task 2 起，每批补真实 Cookie 和写保护 |
| `backend/tests/integration/test_skill_project_permissions_postgres.py` | Task 1 迁移边界 |
| `backend/tests/integration/test_skill_control_plane_postgres.py` | Tasks 3–5 真正事务与并发行为 |
| `backend/tests/integration/test_skill_control_plane_minio.py` | Tasks 3–5 默认存储工厂往返和失败边界 |

`ProjectSkillService(session_factory: Callable[[], Session], *, storage_factory: Callable[[], SkillPackageStorage]=create_default_skill_package_storage, audit_recorder: AuditRecorder | None=None)` 统一接收可调用会话工厂、可调用存储工厂及 AuditRecorder。身份依赖继续使用 get_session；服务创建自己的短生命周期 Session，因此不受身份查询的 autobegin 或外部脏对象影响。DTO 构造在会话内完成，出会话后不触发 ORM 懒加载。`_service() -> ProjectSkillService` 注入现有 core.database.SessionFactory，不在构造器中调用 storage_factory。构造参数不得使用函数定义时已执行的工厂调用。

`project_errors.py` 定义 `ProjectSkillError(code: str, status_code: int)`，异常公开文本就是稳定 code，不挂载内部异常详情。路由只捕获它；仓储和存储的已知异常在服务边界定向转换。Pydantic 仍用默认 422；未知异常 rollback 后原样抛给通用 500 处理，不用 catch Exception→422。

Task 1 授权结果是冻结的 `SkillAccess(scope: SkillScope, owner_ids: frozenset[str] | None)`。None 表示当前项目全部所有者；非空集合用于 created_by IN 查询；空集合不能代表全部可见。

## Task 1: 项目授权和增量权限迁移

**Files:** 新增 project_access.py、project_errors.py、project_support.py、test_project_access.py、权限迁移及 test_skill_project_permissions_postgres.py；修改 identity/catalogue.py、tests/identity/test_bootstrap.py、tests/integration/test_postgres_migrations.py。

**Consumes:** RequestContext、AuthorizationContext、PermissionGrant、ResourceScope、AuthorizationService、Project、SkillScope，以及实际迁移 head `20260908_26`。

**Produces:** `require_skill_access(session: Session, context: RequestContext, permission: str) -> SkillAccess`；`SkillAccess.scope` 和 `.owner_ids`；上述 ProjectSkillError。授权失败 code 为 skill_authentication_required / skill_project_required / skill_permission_denied，分别 401 / 403 / 403。

- [ ] **Step 1: 建测试 fixture 并写授权失败测试。** project_support.py 提供 `sessions`（tmp_path SQLite、FK 开启、每次独立 Session）和 `make_context(*permissions, user_id="user-1", unit_id="unit-1", project_id="project-1", data_scope="project") -> RequestContext`。fixture 提交 active Unit、User、Project、UnitMembership、ProjectMembership，使用显式 flush 顺序满足外键；unit-1 下有 user-1/user-2 和 project-1/project-2，另有 unit-2 和独立用户/项目，不为同一用户建立两个 active 单位成员关系。上下文由真实 PermissionGrant 构造，不根据旧 role 字符串返回预设授权结果。fixtures 由各测试文件显式 import，不修改共享 conftest 的旧 fixture。

```python
def test_missing_manage_grant_is_denied(sessions):
    context = make_context("skill.read")
    with sessions() as session:
        with pytest.raises(ProjectSkillError) as caught:
            require_skill_access(session, context, "skill.manage")
    assert caught.value.status_code == 403
    assert caught.value.code == "skill_permission_denied"

def test_own_scope_returns_only_granted_owner(sessions):
    context = make_context("skill.read", data_scope="own")
    with sessions() as session:
        access = require_skill_access(session, context, "skill.read")
    assert access.scope == SkillScope("unit-1", "project-1")
    assert access.owner_ids == frozenset({"user-1"})
```

独立用例覆盖缺失 authorization_context；user/unit/project 三种不一致；空/不存在/跨单位/停用项目；只有其他项目 grant；unit、project、assigned_projects、custom_projects、own 混合授权。被测函数真实查询 Project，并调用 AuthorizationService，不 mock 授权返回值。

- [ ] **Step 2: 运行 RED。** 执行 `python -m pytest backend/tests/skills/test_project_access.py -q`（本计划所有 python 均用前述解释器）。记录缺模块以外的行为失败；最小接口建立后必须看到越权拒绝和 owner 集合断言的 RED。

- [ ] **Step 3: 实现窄授权转换。** 比较上下文三元组；确认 active Project(unit_id,id)。遍历该操作的 grant 时用 `authorization.model_copy(update={"grants": (grant,)})` 调用现有 AuthorizationService.allows：非 own grant 以 owner=None 测试当前项目，允许则 owner_ids=None；own grant 用实际 `grant.owner_user_id or authorization.user_id` 测试并收集允许 owner。没有有效 grant 抛 403。

```python
owners: set[str] = set()
for grant in authorization.grants:
    single = authorization.model_copy(update={"grants": (grant,)})
    owner = (grant.owner_user_id or authorization.user_id) if grant.data_scope == "own" else None
    target = ResourceScope(context.unit_id, context.project_id, owner)
    if not AuthorizationService().allows(single, permission, target):
        continue
    if grant.data_scope != "own":
        return SkillAccess(SkillScope(context.unit_id, context.project_id), None)
    owners.add(owner)
if not owners:
    raise ProjectSkillError("skill_permission_denied", 403)
return SkillAccess(SkillScope(context.unit_id, context.project_id), frozenset(owners))
```

创建资源时若只有 own 授权，还须确认 context.user_id 在 owner_ids 中；不能用他人的 own 范围替自己创建资源。所有 owner 匹配都基于实际资源 created_by。

- [ ] **Step 4: 写并运行目录与迁移 RED。** 在 test_bootstrap.py 的独立 EXPECTED_ROLE_PERMISSION_CODES 中仅给 project_admin 增加 skill.manage，旧菜单期望不变；先运行该文件看到旧目录缺项失败。迁移测试在专用 migration 库升级到 26，用冻结的 Core 语句种旧权限资料，不能使用已经包含新授权的当前 seed 伪造升级前状态。内置项目管理员、停用内置角色、自定义同码角色分处不同单位以满足 code 唯一约束；保留额外授权，然后升级 head：仅内置 project scope 角色补一项；已存在项不重复，其他授权/状态/菜单相同。新空库无角色时迁移可完成，随后正常 bootstrap 会得到新授权。

- [ ] **Step 5: 实现增量迁移及默认目录。** 父 head 仍为 26 时创建 27；若远端新增 head，先协调实际链并同步本计划和精确断言，不能并行造第二 head。迁移使用冻结的 SQLAlchemy Core 表，不导入可变业务模型或调用 seed_builtin_catalogue。

```python
roles = sa.table("roles", sa.column("id"), sa.column("unit_id"),
                 sa.column("code"), sa.column("built_in"), sa.column("scope_type"))
grants = sa.table("role_permissions", sa.column("id"), sa.column("unit_id"),
                  sa.column("role_id"), sa.column("permission_code"), sa.column("data_scope"))
connection = op.get_bind()
for role in connection.execute(sa.select(roles.c.id, roles.c.unit_id).where(
    roles.c.code == "project_admin", roles.c.built_in.is_(True), roles.c.scope_type == "project"
)).mappings():
    exists = connection.scalar(sa.select(grants.c.id).where(
        grants.c.role_id == role["id"], grants.c.permission_code == "skill.manage",
        grants.c.data_scope == "project"
    ))
    if exists is None:
        connection.execute(grants.insert().values(
            id=str(uuid4()), unit_id=role["unit_id"], role_id=role["id"],
            permission_code="skill.manage", data_scope="project"
        ))
```

该迁移是单一迁移执行器下的追加操作；缺少应有 permission 行导致 FK 失败时明确阻断，不无声跳过。downgrade 明确 no-op 保留授权，注释解释无法安全区分后续人工授予；不删除授权。更新 test_postgres_migrations.py 中两个精确 head 断言为 27。

- [ ] **Step 6: GREEN、审查和定向提交。**

```powershell
& '.venv/Scripts/python.exe' -m pytest backend/tests/skills/test_project_access.py backend/tests/identity/test_bootstrap.py backend/tests/identity/test_authorization.py backend/tests/integration/test_postgres_migrations.py::test_migration_graph_has_single_integration_head -q -p no:cacheprovider
& '.venv/Scripts/python.exe' -m pytest backend/tests/integration/test_skill_project_permissions_postgres.py -q -p no:cacheprovider
```

第二条使用 migration 专用库 TEST_DATABASE_URL，必须无跳过并记录真实行差异；不要运行整个可能 downgrade 的迁移测试文件。审核其他权限不变，提交 `feat: authorize project skill management`，只暂存本任务 Files。

## Task 2: 作用域查询和只读 API

**Files:** 新增 project_schemas.py、project_service.py、project_router.py、test_project_reads.py、test_project_api.py、test_project_cookie_api.py；扩展 repository.py、project_support.py。禁止编辑 app/main.py、旧 router.py 和旧 schemas.py。

**Consumes:** Task 1 的 require_skill_access / SkillAccess / ProjectSkillError；Skill、SkillDraft、SkillVersion 和现有作用域仓储。

**Produces:** `ProjectSkillService` 的 `list(context, *, offset=0, limit=20, q=None) -> SkillPage`、`get(context, skill_id) -> SkillSummary`、`get_draft(context, skill_id) -> SkillDraftInfo`、`list_versions(context, skill_id, *, offset=0, limit=20) -> SkillVersionPage`、`get_version(context, skill_id, version_id) -> SkillVersionInfo`；router 及 `_service()` 依赖。

仓储新增 `list_summaries(scope, *, owner_ids=None, offset=0, limit=20, q=None) -> tuple[list[Mapping[str, Any]], int]`、`get_summary(scope, skill_id, *, owner_ids=None) -> Mapping[str, Any] | None`、`get_draft(scope, skill_id, *, owner_ids=None) -> SkillDraft | None`、`list_version_summaries(scope, skill_id, *, owner_ids=None, offset=0, limit=20) -> tuple[list[Mapping[str, Any]], int]`。现有 get/get_version 增加默认 None 的 keyword-only owner_ids，旧调用不变；owner_ids 空集合必须匹配零行。

DTO 均 extra=forbid，只显式投影以下字段；时间沿用数据库时间值，序列化为 ISO 8601：

- `SkillSummary`: id、name、description、display_version、draft_revision、published_version_id、created_at、updated_at。description/display_version 来自当前草稿；updated_at 为资源/草稿较新时间。
- `SkillFileInfo`: path、size、sha256；文件列表最多沿用 500 条目，不含文件正文。
- `SkillDraftInfo`: skill_id、name、description、display_version、revision、content、files、package_digest、updated_at。
- `PublishedSkillInfo`: id、skill_id、version、source_revision、name、description、display_version、package_digest、published_by、published_at。
- `SkillVersionInfo`: PublishedSkillInfo 加 content、files。版本列表不含这两个大字段。
- `SkillPage` 和 `SkillVersionPage`: items（分别为摘要类型数组）、total、offset、limit。

响应标识符均为 str，published_version_id 为 str | None；数值 version/source_revision/revision/draft_revision/total/offset/limit/size 均为 int，时间字段为 datetime，文本和摘要为 str，files 为 list[SkillFileInfo]。请求路径使用 UUID 并以 str(uuid) 传入服务；不能把 DTO.id 改为 UUID 对象而破坏跨任务字符串比较。SkillDraftInfo/SkillVersionInfo 的 content 响应字段不施加 JSON 输入的 200,000 字符限制。

查询请求定义 `ProjectSkillPageQuery`（offset、limit）与继承它的 `ProjectSkillListQuery`（加 q），均 extra=forbid；使用当前 FastAPI 支持的 Query model，未知归属字段422，避免单个 Query 参数默默忽略客户端 unit_id。offset/limit 接受 URL 整数字符串，revision 的严格整数规则不套用到分页字符串。

- [ ] **Step 1: 补真实测试资料与读取 RED。** project_support.py 复用现有 `tests.skills.test_package_storage.MemoryS3`，不要使用只丢弃上传的 PackageSink。提供 `memory_s3`、`storage` fixtures，以及 `manifest(name, body="Initial instructions") -> str`（yaml.safe_dump name/description/version）和 `bundle(entries: list[tuple[str, bytes]]) -> bytes`（BytesIO + ZipFile）。`seed_skill(sessions, storage, context, *, name="s", body="Initial instructions", attachments=()) -> str` 通过 parse_skill_bundle、storage.put、SkillRepository.create 并提交，返回 UUID；attachments 是相对路径/字节二元组。

```python
def test_own_filter_is_applied_before_pagination_and_count(sessions, storage):
    own = make_context("skill.read", data_scope="own")
    other = make_context("skill.read", user_id="user-2")
    seed_skill(sessions, storage, other, name="hidden")
    expected = seed_skill(sessions, storage, own, name="visible")
    service = ProjectSkillService(sessions, storage_factory=lambda: storage)
    page = service.list(own, offset=0, limit=1)
    assert page.total == 1
    assert [item.id for item in page.items] == [expected]

def test_read_does_not_construct_storage(sessions, storage):
    context = make_context("skill.read")
    skill_id = seed_skill(sessions, storage, context)
    def forbidden_storage():
        raise AssertionError("Metadata reads must not contact storage")
    service = ProjectSkillService(sessions, storage_factory=forbidden_storage)
    assert service.get(context, skill_id).id == skill_id
```

补充各读取路径的跨项目/单位/own 404、无入口权限403、无身份401；给可见 Skill 配置另一个 Skill 的 version_id 必须404。版本 fixture 使用现有仓储 publish 在测试 setup 中生成，不提前调用未实现的发布服务。SQL 观察应确认列表仅选择摘要列，不装载所有 content/files，也不按每行额外查询草稿。

- [ ] **Step 2: 运行 RED。** `python -m pytest backend/tests/skills/test_project_reads.py backend/tests/skills/test_project_api.py -q`。优先记录 owner 过滤、跨 scope 和敏感字段断言失败，不把404路由缺失当成最终授权测试证据。

- [ ] **Step 3: 实现查询投影及独立服务。** 查询条件统一组合 scope、owner_ids、q，count 与 items 复用同一个 WHERE。使用 `Skill.name.contains(q, autoescape=True)`，offset/limit 在 SQL 执行；资源摘要 join SkillDraft，具体列用 `.mappings()` 转 DTO。版本列表查询先确认可见 Skill，空历史返回空数组而非404。读方法只打开服务自有会话，不 commit。

```python
conditions = [Skill.unit_id == scope.unit_id, Skill.project_id == scope.project_id]
if owner_ids is not None:
    conditions.append(Skill.created_by.in_(owner_ids))
if q is not None:
    conditions.append(Skill.name.contains(q, autoescape=True))
total = session.scalar(select(func.count()).select_from(Skill)
                       .join(SkillDraft, SkillDraft.skill_id == Skill.id).where(*conditions))
```

每个服务读方法重新检查传入授权上下文与当前 active Project，传完整 owner_ids 给仓储；找不到对应范围资源时抛稳定404。

- [ ] **Step 4: 写 HTTP 和真实 Cookie 测试并实现路由。** 路由 prefix 固定 `/api/project-skills`，注册根路径空字符串（不依赖斜杠重定向）。GET /、/{id}、/{id}/draft、/{id}/versions、/{id}/versions/{version_id} 均依赖 require_request_context；读取返回 no-store。UUID 路径只转规范字符串交给服务，普通 name 路径422。

```python
@router.get("", response_model=SkillPage)
def list_skills(response: Response, context: RequestContext = Depends(require_request_context),
                service: ProjectSkillService = Depends(_service),
                query: ProjectSkillListQuery = Query()):
    response.headers["Cache-Control"] = "no-store"
    try:
        return service.list(context, offset=query.offset, limit=query.limit, q=query.q)
    except ProjectSkillError as error:
        raise HTTPException(error.status_code, error.code) from error
```

project_support 增加 `make_test_app(sessions, storage_factory, *, context=None, with_write_protection=False) -> FastAPI`：只挂新 router；get_session 用 yield/finally 关闭身份会话；_service 使用同一 factory 但创建不同服务 Session。context 非空时仅授权单元测试覆盖 require_request_context；Cookie 测试必须 context=None 且 app.state.allow_dev_identity=False。

`issue_session(sessions, *, user_id="user-1", project_id="project-1", role_code="project_admin") -> str` 在测试库建立真实内置角色绑定、项目/单位成员关系及 AuthSession，token 使用 secrets.token_urlsafe、仅存 sha256，当前授权版本匹配且两项有效期在未来。测试失效会话/成员撤销/角色权限撤销（使用自定义角色）后下一请求被拒绝、伪造 X-* 不改变 Cookie 身份、不选择其他项目。参考 identity/test_auth_api.py 的数据字段但不要复用其全局 app 覆盖方式。

- [ ] **Step 5: GREEN、审查和提交。**

```powershell
& '.venv/Scripts/python.exe' -m pytest backend/tests/skills/test_project_reads.py backend/tests/skills/test_project_api.py backend/tests/skills/test_project_cookie_api.py backend/tests/skills/test_repository.py backend/tests/test_skills.py -q -p no:cacheprovider
```

用独立子进程 import project_router 并观测默认存储工厂/旧 SkillService 未构造；用测试断言 app.main 路由列表无 `/api/project-skills` 且旧路径不变。审查完成后定向提交 `feat: add scoped skill read API`。

## Task 3: 有界包处理与草稿创建/保存

**Files:** 新增 project_packages.py、test_project_packages.py、test_project_drafts.py、test_skill_control_plane_postgres.py、test_skill_control_plane_minio.py；扩展 project_service.py、project_schemas.py、project_router.py、repository.py、test_project_api.py、test_project_cookie_api.py 和 project_support.py。

**Consumes:** Task 1 授权结果；Task 2 DTO、service 的 session_factory/storage_factory 和显式响应约定；parse_skill_bundle、ValidatedSkillPackage、SkillPackageStorage、StoredSkillPackage、AuditRecorder。

**Produces:** `create(context, request: ProjectSkillCreate) -> SkillSummary`；`save_draft(context, skill_id, request: ProjectSkillDraftUpdate) -> SkillDraftInfo`。仓储公开 `lock_skill(scope, skill_id, *, owner_ids=None) -> Skill`，沿用原 _lock 的行锁和 populate_existing 语义，不改旧方法默认行为。

服务新增 `check_access(context: RequestContext, permission: str) -> SkillAccess`：短只读自有会话调用 require_skill_access 后关闭。路由新增 `_manage_context(context=Depends(require_request_context), service=Depends(_service)) -> RequestContext`，调用 check_access(context,"skill.manage")，定向转换 ProjectSkillError 后返回 context。POST/PUT/import/publish 均复用该依赖，服务方法内部仍重新授权；它是上传业务读取前的权限检查，不是解析器替身。

包模块新增冻结 `DraftPackageSnapshot`：skill_id、revision、name、description、display_version、content、files（tuple[tuple[str, int, str], ...]，依次为 path/size/sha256）、stored（StoredSkillPackage）。`snapshot_draft(row: SkillDraft) -> DraftPackageSnapshot` 在会话内深复制字段；`read_verified_package(storage, snapshot) -> ValidatedSkillPackage` 比较存储引用和解析结果；`package_from_content(content: str) -> ValidatedSkillPackage` 构造只有 SKILL.md 的包；`replace_manifest(package, content: str) -> ValidatedSkillPackage` 保留其他文件并完整重验。Task 4 会补 rename_manifest，不提前支持引用读取。

- [ ] **Step 1: 写行为 RED。** request 使用 Pydantic ConfigDict(extra="forbid")；ProjectSkillCreate 只有 content（1–200,000 字符）；ProjectSkillDraftUpdate 只有 expected_revision（严格正整数）和 content。测试创建不接收归属字段、创建只产生草稿、同名409、无权限不构造存储；旧 SkillService 目录不产生文件。

```python
def test_edit_preserves_reference_bytes(sessions, storage):
    context = make_context("skill.read", "skill.manage")
    skill_id = seed_skill(sessions, storage, context,
                          attachments=(("references/rules.txt", b"check inflow"),))
    service = ProjectSkillService(sessions, storage_factory=lambda: storage)
    result = service.save_draft(context, skill_id, ProjectSkillDraftUpdate(
        expected_revision=1, content=manifest("s", "Changed instructions")
    ))
    with sessions() as session:
        snapshot = snapshot_draft(SkillRepository(session).get_draft(
            SkillScope(context.unit_id, context.project_id), skill_id))
    package = read_verified_package(storage, snapshot)
    assert result.revision == 2
    assert {item.path: item.data for item in package.files}["references/rules.txt"] == b"check inflow"

def test_stale_save_keeps_committed_draft(sessions, storage):
    context = make_context("skill.read", "skill.manage")
    skill_id = seed_skill(sessions, storage, context)
    service = ProjectSkillService(sessions, storage_factory=lambda: storage)
    saved = service.save_draft(context, skill_id, ProjectSkillDraftUpdate(
        expected_revision=1, content=manifest("s", "First edit")))
    with pytest.raises(ProjectSkillError) as caught:
        service.save_draft(context, skill_id, ProjectSkillDraftUpdate(
            expected_revision=1, content=manifest("s", "Stale edit")))
    assert caught.value.code == "skill_revision_conflict"
    assert service.get_draft(context, skill_id).content == saved.content
```

审计失败测试注入 `AuditRecorder` 边界的实例替身 record 抛 RuntimeError，随后用新 Session 核对 Skill/SkillDraft 未变，不只断言抛异常。存储失败/归档摘要篡改/文件清单不一致各有真实损坏数据测试。附件上传后的旧对象仍可读；JSON 改名422，超长输入422，导入形成的大正文不能在响应端被截断。

- [ ] **Step 2: 跑 RED。** `python -m pytest backend/tests/skills/test_project_packages.py backend/tests/skills/test_project_drafts.py -q`，记录附件被丢弃、陈旧写覆盖或审计不原子等实际失败后才实现。

- [ ] **Step 3: 实现包重建和快照验证。** 使用 BytesIO/zipfile 重新编码，调用 parse_skill_bundle 获取可信类型，不手工修改 package.digest；不解包到磁盘。共享内部 `_parse_files(entries: list[tuple[str, bytes]]) -> ValidatedSkillPackage` 实现如下，两个公开纯函数调用它。

```python
def _parse_files(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, data in entries:
            archive.writestr(path, data)
    packages = parse_skill_bundle(stream.getvalue())
    if len(packages) != 1:
        raise SkillPackageError("A single skill package is required")
    return packages[0]

def replace_manifest(package, content):
    entries = [(item.path, content.encode("utf-8") if item.path == "SKILL.md" else item.data)
               for item in package.files]
    return _parse_files(entries)

def package_from_content(content):
    return _parse_files([("SKILL.md", content.encode("utf-8"))])
```

`read_verified_package` 先 storage.read(snapshot.stored)，再解析；恰好一个包。按顺序比对 name、description、display_version、content、规范化 digest 和 `(path,size,sha256)` 清单；不一致转换 skill_storage_unavailable(503)。读取边界的结构损坏/路径问题也属已存对象完整性失败503，用户新输入包错误422，两者不能混淆。仓储拿到的对象引用还须验证其路径前三段与当前 scope/skill_id 一致再访问，不接受篡改引用指向其他项目。

- [ ] **Step 4: 实现自有事务与成功审计。** 创建：短只读会话授权和同名预检 → UUID/包验证 → 对象 put → 新会话写事务再次授权、创建、审计、commit。保存：短只读会话获取可见草稿并验证 revision，脱离 ORM 为冻结 snapshot 后关闭会话 → 验证/替换/put → 新事务按 owner 范围 lock_skill、刷新草稿、核对旧 snapshot、save_draft、审计、commit。行锁期间没有对象 I/O。

```python
with self.session_factory() as session:
    try:
        with session.begin():
            access = require_skill_access(session, context, "skill.manage")
            repository = SkillRepository(session)
            repository.lock_skill(access.scope, skill_id, owner_ids=access.owner_ids)
            current = repository.get_draft(access.scope, skill_id, owner_ids=access.owner_ids)
            if current is None or snapshot_draft(current) != original_snapshot:
                raise ProjectSkillError("skill_revision_conflict", 409)
            draft = repository.save_draft(access.scope, skill_id,
                expected_revision=request.expected_revision, package=package, stored=stored)
            self._record_change(session, context, skill_id, "skill.draft.save",
                                revision=draft.revision, digest=stored.package_digest)
            result = SkillDraftInfo.model_validate(self._draft_fields(draft))
    except Exception:
        session.rollback()
        raise
return result
```

`_draft_fields(row) -> dict[str, object]` 只投影 Task 2 指定字段；`_record_change(session, context, skill_id, action, *, revision, digest, version_id=None, trace_id=None) -> None` 用 AuditRecordRequest(category="management", source="system", resource_type="skill", event_scope="project", authorization_scope="project", status="succeeded", risk_level="medium")。unit_id/project_id/user_id 来自 context，actor_roles=context.role_codes，auth_method 来自授权上下文，occurred_at=datetime.now(UTC)；metadata allowlist 为 revision/digest/version_id。trace_id 默认 uuid4；普通变更审计键 `skill:{skill_id}:{action}:{uuid4()}`，传 version_id 时用 `skill:{skill_id}:publish:{version_id}`。审计与事务同 Session，绝不自行 commit。错误转换仅识别 uq_skills_scope_name / SkillRevisionConflict / 已知包或存储异常；未知 IntegrityError 仍是500。

- [ ] **Step 5: 添加 HTTP 写路由及实际 CSRF 装配测试。** POST 根返回201，PUT /{id}/draft 返回200，均为同步 def，调用上述服务。写输入 extra forbid 和 strict revision 在 Schema 验证。Cookie 测试 app 通过 `app.middleware("http")(app.main.disable_auth_response_caching)` 挂实际中间件函数，不能复制一个只用于测试的近似实现；导入 main 仅在临时目录/隔离子进程及明确测试数据库环境中进行，关闭启动后台调度，不运行生产 lifespan，也不修改 global app.routes。

```python
token = issue_session(sessions)
app = make_test_app(sessions, lambda: storage, with_write_protection=True)
with TestClient(app) as client:
    client.cookies.set("iap_session", token)
    blocked = client.post("/api/project-skills", json={"content": manifest("s")})
    assert blocked.status_code == 403
    accepted = client.post("/api/project-skills", json={"content": manifest("s")},
                           headers={"X-CSRF-Token": "test-present"})
    assert accepted.status_code == 201
```

这里仅验证现有“头存在”策略，不宣称已验证 CSRF token 绑定。补 Origin 不匹配拒绝测试，settings.public_base_url 仅临时 monkeypatch 后恢复。带伪造管理员/其他项目头的低权限 Cookie 仍拒绝；无认证有效请求401，且对象未新增。

- [ ] **Step 6: GREEN、真实读写与提交。**

```powershell
& '.venv/Scripts/python.exe' -m pytest backend/tests/skills/test_project_packages.py backend/tests/skills/test_project_drafts.py backend/tests/skills/test_project_api.py backend/tests/skills/test_project_cookie_api.py -q -p no:cacheprovider
& '.venv/Scripts/python.exe' -m pytest backend/tests/integration/test_skill_control_plane_postgres.py backend/tests/integration/test_skill_control_plane_minio.py -q -p no:cacheprovider
```

第二条进程注入本计划专用 TEST_DATABASE_URL/TEST_S3_*，无配置的 skip 不能过关。PostgreSQL 检查真实创建/保存和审计原子回滚；MinIO 默认工厂只把桶配置换成 fixture UUID 桶，实际存取并保留附件。审查通过后提交 `feat: create and revise project skill drafts`。

## Task 4: 原子批量导入

**Files:** 扩展 project_schemas.py、project_packages.py、project_service.py、project_router.py、repository.py、test_project_api.py、test_project_cookie_api.py、两份服务集成测试；新增 test_project_import.py。

**Consumes:** Tasks 1–3 的包验证、作用域锁、审计与错误契约；UUID 及每个目标 expected_revision。导入不调用逐项自行 commit 的 create/save_draft 服务。

**Produces:** `import_bundle(context, data: bytes, manifest_json: str | None) -> SkillImportResult`。包模块 `rename_manifest(package: ValidatedSkillPackage, target_name: str) -> ValidatedSkillPackage`；响应 `SkillImportResult(items: list[SkillImportItem], created_count:int, updated_count:int, skipped_count:int)`。每项字段 source_name、action、skill_id（skip 为 null）、name（skip 为 source_name）、draft_revision（skip 为 null），不返回对象位置或完整正文。

- [ ] **Step 1: 写 manifest 和原子行为 RED。** Pydantic discriminated union 三种类型：ProjectSkillImportCreate（action="create", source_name,target_name 可选）、ProjectSkillImportUpdate（action="update", source_name,skill_id,expected_revision）、ProjectSkillImportSkip（action="skip",source_name），每类 extra forbid；ProjectSkillImportEntry 是 Annotated 三类型联合、Field(discriminator="action")。缺失 manifest 默认全部 create；显式数组必须覆盖全部已解析 source_name 且无重复。source_name/target_name 应满足现有清单 name 规则，不增加与包解析器不同的名称标准。

```python
def test_import_does_not_partially_commit_when_second_object_fails(sessions, memory_s3):
    class FailSecondPut(type(memory_s3)):
        def put_object(self, **request):
            if len(self.objects) == 1:
                raise OSError("simulated store failure")
            return super().put_object(**request)
    failing_storage = SkillPackageStorage(FailSecondPut(), "test-skills")
    service = ProjectSkillService(sessions, storage_factory=lambda: failing_storage)
    data = bundle([("a/SKILL.md", manifest("a").encode()),
                   ("b/SKILL.md", manifest("b").encode())])
    with pytest.raises(ProjectSkillError) as caught:
        service.import_bundle(make_context("skill.manage"), data, None)
    assert caught.value.status_code == 503
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Skill)) == 0
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 0
```

增加混合 create/update 第二目标 revision 陈旧、审计第二条失败和数据库提交失败的完整回滚断言，先保存旧草稿内容/revision/已发布指针供比较；skip 不读取目标信息且不上传；默认同名409；显式改名后包摘要变化、正文内普通同名字符串不被替换。禁止重复目标 UUID 和创建名在批内冲突。

- [ ] **Step 2: 跑 RED。** `python -m pytest backend/tests/skills/test_project_import.py -q`，确认出现单项提交或重命名数据错误等行为失败；服务正常导入尚未实现不能算全部异常用例验证完成。

- [ ] **Step 3: 实现解析、授权与全批准备。** 先认证/项目及 manage 入口，再 parse_skill_bundle 完整 ZIP，再验证 manifest 字节数/项数/一一对应、动作额外字段和目标名规则。`rename_manifest` 复用 service.py 的纯 update_manifest(content,name=target_name)，它使用 yaml.safe_load/safe_dump；随后 replace_manifest 完整重验，不能实例化旧 SkillService。update 的名称固定为已授权目标资源名称。

所有目标完成授权与revision预检后才进行对象上传，不因前一项上传成功就开始数据库写事务。创建资源预分配 UUID；update 记录原冻结 snapshot。准备结果可用冻结内部 `PreparedSkillImport(source_name, action, skill_id, name, expected_snapshot, package, stored)`，action 仅 create/update；skip 单独保留响应顺序。expected_snapshot create为None；所有类型均为 Task 3 已定义类型。

```python
created = {package.name for package in packages}
planned = [entry.source_name for entry in entries]
if len(planned) != len(set(planned)) or set(planned) != created:
    raise ProjectSkillError("skill_import_manifest_invalid", 422)
update_ids = [str(entry.skill_id) for entry in entries if entry.action == "update"]
if len(update_ids) != len(set(update_ids)):
    raise ProjectSkillError("skill_import_manifest_invalid", 422)
```

- [ ] **Step 4: 实现单一写事务。** Task 3 的 lock_skill 按排序后的全部 update UUID 先加锁，再重新检查每项 owner 范围和 snapshot。按已确定的输入顺序调用 repository.create/save_draft，追加同一 trace_id 的 skill.import 审计，所有项完成后一次 commit。skip 不写审计；全 skip 返回成功但不创建存储客户端。同名预检查询限定 scope，冲突仅返回通用 skill_name_conflict，不返回不可见资源ID。真正唯一竞争由 uq_skills_scope_name 捕获后回滚整个事务。

```python
with self.session_factory() as session:
    with session.begin():
        access = require_skill_access(session, context, "skill.manage")
        repository = SkillRepository(session)
        for skill_id in sorted(update_ids):
            repository.lock_skill(access.scope, skill_id, owner_ids=access.owner_ids)
        for item in prepared:
            self._apply_import(session, repository, context, access, item, trace_id)
```

`_apply_import(session,repository,context,access,item,trace_id) -> SkillImportItem` 只执行该项 create 或在比较 expected_snapshot 后 save_draft，并调用 _record_change；禁止 commit。异常处理沿 Task 3 所有会话关闭和 rollback 规则。保留孤立对象，不向调用者给出可读对象键。

- [ ] **Step 5: 加有界上传路由和端到端测试。** POST /import 注册在动态 UUID 路由前，采用同步 def 接收 UploadFile/File 和 manifest/Form。使用 Task 3 的 _manage_context 在业务读文件前检查 manage；helper `_read_upload(file: UploadFile) -> bytes` 从 file.file 分块最多读取 MAX_ZIP_BYTES+1，finally 关闭底层文件；超过返回413，合法数据传给 import_bundle（服务仍自行重新授权）。不把文件扩展名当安全判断。

新增异步校验依赖 `_validate_import_fields(request: Request) -> None`：读取框架已经解析的 FormData，允许恰好一个 file、零或一个 manifest，拒绝未知字段及重复外层字段为422；使用 FormData.multi_items 计数，不重写 multipart 解析器。依赖不持有业务 Session，不做对象I/O。所有拒绝路径由框架上下文关闭上传文件，并用资源关闭测试确认。

```python
def _read_upload(file):
    chunks = []
    size = 0
    try:
        while chunk := file.file.read(min(65536, MAX_ZIP_BYTES + 1 - size)):
            size += len(chunk)
            if size > MAX_ZIP_BYTES:
                raise ProjectSkillError("skill_upload_too_large", 413)
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        file.file.close()
```

同步路由避免跨线程共享 Session：授权依赖只返回上下文，不传递 Session；服务授权检查结束关闭自有会话，业务操作使用新会话。测试文件超限时关闭、未知JSON动作/重复source_name/manifest超限422、未授权不做业务 parse 或存储请求。框架 multipart 暂存行为不在业务上传 helper 的资源承诺内。

- [ ] **Step 6: GREEN、真实原子验证和提交。**

```powershell
& '.venv/Scripts/python.exe' -m pytest backend/tests/skills/test_project_import.py backend/tests/skills/test_project_api.py backend/tests/skills/test_project_cookie_api.py backend/tests/skills/test_project_packages.py -q -p no:cacheprovider
& '.venv/Scripts/python.exe' -m pytest backend/tests/integration/test_skill_control_plane_postgres.py backend/tests/integration/test_skill_control_plane_minio.py -q -p no:cacheprovider
```

真实 PostgreSQL 双连接按 Barrier/Event 确认唯一竞争和更新锁等待；不使用 sleep 推测另一事务已开始。MinIO 验证批内全部对象可按数据库引用读回、失败时数据库完全未变。定向提交 `feat: import project skill drafts atomically`。

## Task 5: 复验、幂等发布和同事务审计

**Files:** 扩展 repository.py、project_service.py、project_schemas.py、project_router.py、test_project_api.py、test_project_cookie_api.py、test_skill_control_plane_postgres.py、test_skill_control_plane_minio.py；新增 test_project_publish.py；补 backend/README.md 的隔离接口和验证说明。

**Consumes:** Task 3 DraftPackageSnapshot、read_verified_package、lock_skill 和 _record_change；Task 2 PublishedSkillInfo；原仓储 publish 的 revision消耗/行锁/不可变快照行为。

**Produces:** `publish(context, skill_id, request: ProjectSkillPublish, idempotency_key: str) -> PublishedSkillInfo`；ProjectSkillPublish 仅 expected_revision。仓储 `find_publish_replay(scope, skill_id, *, expected_revision, idempotency_key, published_by, owner_ids=None) -> SkillVersion | None`；内部 `_publish_request_digest(skill_id, expected_revision, published_by) -> str` 提取原来完全相同的 canonical JSON 摘要算法，由 find_publish_replay 和旧 publish 共同调用，历史键和摘要不得变化。

- [ ] **Step 1: 先写关键 RED。** 通过公开服务 publish，使用真实包和 MemoryS3，不 mock repository.publish。

```python
def test_publish_replay_survives_later_draft_and_storage_failure(sessions, storage):
    context = make_context("skill.read", "skill.manage")
    skill_id = seed_skill(sessions, storage, context)
    service = ProjectSkillService(sessions, storage_factory=lambda: storage)
    request = ProjectSkillPublish(expected_revision=1)
    first = service.publish(context, skill_id, request, "publish-1")
    service.save_draft(context, skill_id, ProjectSkillDraftUpdate(
        expected_revision=2, content=manifest("s", "Later draft")))
    def unavailable():
        raise AssertionError("A committed replay must not initialize storage")
    replay = ProjectSkillService(sessions, storage_factory=unavailable).publish(
        context, skill_id, request, "publish-1")
    assert replay.model_dump() == first.model_dump()
    with sessions() as session:
        versions = session.scalar(select(func.count()).select_from(SkillVersion)
                                  .where(SkillVersion.skill_id == skill_id))
        audits = session.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.resource_id == skill_id, AuditEvent.action == "skill.publish"))
    assert versions == 1
    assert audits == 1
```

同键不同 expected_revision/发布用户409；没有 manage 的重放403；原scope外重放404；同名Skill不同UUID不能串键。完整性坏包503保持旧指针，审计/提交失败不消耗revision、不产生版本。参数化缺幂等头、空键、129字符、非ASCII、bool/浮点revision、extra字段422，服务直接调用也验证关键键/revision约束。

- [ ] **Step 2: 运行 RED。** `python -m pytest backend/tests/skills/test_project_publish.py -q`；确认“先读当前草稿再查幂等”和“重复审计”的错误顺序会使上面测试失败。

- [ ] **Step 3: 实现公开幂等查询和短事务状态机。** find_publish_replay 先 scope/owner资源检查，再使用原规范化摘要和 _replay。发布服务按以下顺序执行，不在重放路径触发存储工厂：

1. 短预检会话授权、查既有同键结果；有结果即投影返回，键参数冲突409。
2. 无结果则读取预期草稿为冻结snapshot，关闭读会话。若此时revision已陈旧，重新检查已提交同键结果后再决定409。
3. 会话外 read_verified_package 验证对象和所有快照字段。只有已知存储/包验证错误时可以再用新会话检查是否已有并发同键成功；没有则保持503，不能用此分支掩盖其他异常。
4. 新写会话事务中重新授权、按 owner 范围锁Skill，再查同键结果；若已有则返回相同版本，不再记录成功审计。
5. 未重放时 refresh 草稿并比较 snapshot_draft(current)==verified_snapshot，然后 repository.publish；版本及审计同事务提交。

```python
with self.session_factory() as session:
    with session.begin():
        access = require_skill_access(session, context, "skill.manage")
        repository = SkillRepository(session)
        repository.lock_skill(access.scope, skill_id, owner_ids=access.owner_ids)
        replay = repository.find_publish_replay(access.scope, skill_id,
            expected_revision=request.expected_revision, idempotency_key=idempotency_key,
            published_by=context.user_id, owner_ids=access.owner_ids)
        if replay is not None:
            result = PublishedSkillInfo.model_validate(self._version_fields(replay))
        else:
            current = repository.get_draft(access.scope, skill_id, owner_ids=access.owner_ids)
            if current is None or snapshot_draft(current) != verified_snapshot:
                raise ProjectSkillError("skill_revision_conflict", 409)
            version = repository.publish(access.scope, skill_id,
                expected_revision=request.expected_revision, idempotency_key=idempotency_key,
                published_by=context.user_id)
            self._record_change(session, context, skill_id, "skill.publish",
                revision=version.source_revision, digest=version.package_digest,
                version_id=version.id)
            result = PublishedSkillInfo.model_validate(self._version_fields(version))
return result
```

`_version_fields(row)` 显式投影 PublishedSkillInfo 的固定字段，不携带当前草稿revision；任何异常由外层受控rollback处理。行锁后的第二次仓储publish再检查同键属兼容保留，同一行锁范围内服务为唯一版本写入者，不应再次产生并发新版本。

- [ ] **Step 4: 加发布 HTTP 路由并补全安全矩阵。** POST /{skill_id}/publish 同步路由、200、PublishedSkillInfo；Idempotency-Key Header 必需且打印ASCII校验。API调用只管理固定版本，不接受客户端 object_key、digest或版本号。读取与写入响应各用已定义Schema，不因 ORM 的新增列扩大响应。

```python
IdempotencyKey = Annotated[str, StringConstraints(min_length=1, max_length=128,
                                               pattern=r"^[\x20-\x7e]+$")]
```

project_cookie_api 对所有九个方法逐项验证有效身份、无权限、跨scope，并补发布后权限撤销的再次重放拒绝。复用真实中间件验证发布/导入/保存缺CSRF或跨Origin拒绝；实际授权测试不只覆盖 dependency_overrides。验证 read no-store，含content的写响应也设置 no-store，避免正文被中间缓存。

- [ ] **Step 5: 真正并发/故障测试和 GREEN。** test_skill_control_plane_postgres 用两个服务对象、两条独立连接、共享受控存储边界的 Event/Barrier 建立这些时序：同键同时发布→同版本/一条审计；不同键抢同revision→一成功一409；同键不同用户→一成功一幂等冲突；包验证期间草稿保存→发布409；包验证期间竞争者已成功→重放同版本；取得快照后对象读失败但已有同键成功→重放且不写审计。

```powershell
& '.venv/Scripts/python.exe' -m pytest backend/tests/skills/test_project_publish.py backend/tests/skills/test_project_api.py backend/tests/skills/test_project_cookie_api.py backend/tests/skills/test_repository.py -q -p no:cacheprovider
& '.venv/Scripts/python.exe' -m pytest backend/tests/integration/test_skill_control_plane_postgres.py backend/tests/integration/test_skill_control_plane_minio.py backend/tests/integration/test_skill_versions_postgres.py -q -p no:cacheprovider
```

每个并发测试有有界等待和 finally 释放屏障，线程future必须取得结果；与原TLS计时用例串行运行。故障注入只替代存储/审计/commit边界，并核对真实数据库中的最终快照、指针、revision和审计行，不能只观察函数调用次数。

- [ ] **Step 6: 更新使用说明、审查并提交。** README 写出新接口只在测试装配存在、身份/项目要求、上限、413/409/503含义、孤立对象补偿、权限迁移的追加与非删除降级语义；明确运行时/UI/业务数据未切换且旧接口认证风险未整改。定向提交 `feat: publish verified project skill versions`。

## 最终审查与回归门禁

- [ ] **Step 1: 冻结源码并检查范围。** 记录最终HEAD，确认无前端、旧SkillService/路由、app.main生产挂载、Agent/Team、Gateway业务变化。检查数据库只有追加权限迁移，无旧表数据切换。导出确定基线到HEAD的diff进行全分支审查，复用各任务审查证据，不重新实现已验收基础模块。

```powershell
git diff --check
git diff --stat 316f511 HEAD
git diff 316f511 HEAD -- backend/app/main.py backend/app/skills/router.py backend/app/skills/service.py frontend backend/app/agents backend/app/collaboration backend/app/runtime
```

最后一条预期无输出；若有改动必须逐项说明并检查是否违反范围，不直接接受。

- [ ] **Step 2: 控制端独立运行聚焦及真实集成。** 专用测试注入下执行：

```powershell
& '.venv/Scripts/python.exe' -m pytest backend/tests/skills backend/tests/identity backend/tests/collaboration backend/tests/test_skills.py backend/tests/integration/test_postgres_migrations.py::test_migration_graph_has_single_integration_head -q -p no:cacheprovider
& '.venv/Scripts/python.exe' -m pytest backend/tests/integration/test_skill_control_plane_postgres.py backend/tests/integration/test_skill_control_plane_minio.py backend/tests/integration/test_skill_versions_postgres.py backend/tests/integration/test_skill_package_minio.py backend/tests/integration/test_postgres_migrations.py::test_upgrade_head_creates_conversation_tables -q -p no:cacheprovider
& '.venv/Scripts/python.exe' -m pytest backend/tests/integration/test_skill_project_permissions_postgres.py -q -p no:cacheprovider
```

第二条使用服务集成库，第三条独立进程切换到migration专用库；第一条不注入外部 TEST_*。默认工厂相关MinIO测试另外注入专用桶/连接变量，所有变量用finally恢复，不将环境输出到日志。

- [ ] **Step 3: 一次最终完整后端回归。** 清除所有 TEST_*，保持 DATABASE_URL 仅指向服务测试库，运行 `python -m pytest backend/tests -q -p no:cacheprovider`。记录通过、失败、跳过、警告和耗时；不得把无配置跳过当作已验证。`pip check` 同轮执行；任何失败先诊断，不拿专项GREEN代替完整GREEN。文档不谎称执行过前端/browser测试，本轮无UI代码。

- [ ] **Step 4: 交付报告与Git协调。** 写 `docs/superpowers/plans/2026-09-08-skill-control-plane-verification.md`，逐项记录设计契约、RED/GREEN、迁移/并发、审查处置与残余风险。再次显式验证TLS获取、合并origin/main；有实际远端源码变化则重跑受影响验证。定向提交记录，不推送、不部署。本阶段完成后由用户决定是否合入main，不能把上阶段本地合并授权无限沿用。

## 设计覆盖与计划自检

| 设计要求 | 任务与证据 |
| --- | --- |
| 独立接口、不挂主应用、不动旧链路 | Tasks 2–5 测试装配、最终路径diff及README |
| 会话身份、scope、own、401/403/404 | Task 1 权限函数；Task 2 SQL过滤与Cookie；Tasks 3–5各写接口 |
| project_admin新增项目管理权限、旧角色不变 | Task 1目录期望、专用库26→27迁移及已有/新增单位 |
| 分页、字段投影、无存储凭据 | Task 2 DTO与SQL摘要查询；Task 5全路由矩阵 |
| 有界正文、ZIP、惰性存储及附件保留 | Task 3包helper；Task 4上传/manifest上限 |
| 显式冲突策略和全批原子导入 | Task 4场景测试及真实PostgreSQL/MinIO |
| 对象复验、同键重放、短事务、防止发布未验证草稿 | Task 5状态机、存储失败时重放和真实双连接时序 |
| 同事务成功审计、失败rollback、不泄露正文 | Task 3共享审计；Task 4批次；Task 5版本幂等与失败注入 |
| Cookie写保护、不冒充旧安全整改 | Task 3实际中间件装配；Task 5安全矩阵和README |
| 不清理共享数据、测试与业务隔离 | 环境准备、迁移独立库、最终回归门禁 |

- [x] 已逐项比对设计，每项本批契约映射到上述任务和验收步骤。
- [x] 已检查跨任务方法/类型/fixture名称一致，补齐响应ID类型、上传管理依赖、包快照和审计helper契约。
- [x] 已检查计划占位、错误码冲突、生产切换和额外授权；迁移前状态使用冻结资料，不用当前目录伪造旧数据。
- [ ] 计划定向提交后，由用户选择逐任务子代理执行或当前会话分批执行；没有开始业务实现。

2026-09-08 计划自检记录：当前 Alembic 单 head 实测为 20260908_26；文档代码围栏配对、五个任务标题和已有引用文件检查通过。上述命令只读取迁移图，没有执行upgrade或创建数据库。本轮仅设计状态及实施计划变更，未运行新API功能测试；上阶段和设计分支的历史测试结果不能替代本计划实施证据。
