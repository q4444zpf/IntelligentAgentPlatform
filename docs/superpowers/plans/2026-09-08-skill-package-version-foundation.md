# Skill Package And Version Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Skill 生产化提供安全的文件包解析、不可变对象保存，以及按项目隔离的草稿和发布版本仓储。

**Architecture:** PostgreSQL 保存权威资源及版本，MinIO 保存不可变文件包。先交付不挂载公开路由的基础模块，通过独立测试验证；权限 API、Agent/Team 接入和界面在后续子计划中统一切换，避免中途替换现有调用契约。

**Tech Stack:** Python、Pytest、SQLAlchemy 2、Alembic、PostgreSQL、Boto3/MinIO；复用现有依赖，不引入队列。

## Global Constraints

- 设计依据：`docs/superpowers/specs/2026-09-07-skill-productionization-design.md`。
- Skill 资源使用 UUID；名称只在 `(unit_id, project_id)` 内唯一。
- 压缩包最大 10 MiB，解压实际内容累计最大 20 MiB，ZIP 条目最多 500 个。
- 保存草稿与发布需要预期 revision；已发布内容不可变；展示版本不是版本主键。
- 不执行包内脚本，不安装依赖，不扩展知识库或 Embedding。
- 不导入、清理或切换用户现有数据，不修改历史 Team 版本与 Run 快照。
- 不在主分支实施业务代码；先 fetch 并协调远端 main，再建立 `codex/skill-version-foundation` 工作区。
- Git 操作遇到审批拒绝不得通过其他工具或目录副本绕过。
- 测试存储只使用临时目录及独立测试数据库/桶；跳过集成测试不等于通过。

---

## 执行状态

- [x] 核对现有 Skill、授权、Team、Run、对象存储及迁移接口。
- [x] 根据用户继续执行指示，采用已给出的项目隔离方案。
- [x] 拆出独立基础子计划并检查与整体设计的对应关系。
- [x] 获取远端 main 并协调更新。
- [x] 建立隔离工作区及验证基线。
- [x] Task 1：安全文件包解析（893501a；37 项新测试、5 项原有测试通过，独立审查通过）。
- [x] Task 2：不可变对象保存和有界读取（c3aaa35；解析与存储共 51 项、真实 MinIO 1 项通过，审查无阻断项）。
- [x] Task 3：草稿及版本仓储、数据库迁移和并发验证（a6aa107；84 项聚焦回归、9 项真实 PostgreSQL/迁移检查通过，独立审查通过）。
- [x] 基础模块审查、专项及真实集成验证、源码定向提交（最终修复 a33e49f；93 项聚焦回归、10 项真实集成/迁移检查通过，复审通过）。
- [x] 验收文档提交：最终源码已合并并验证，文档已整理进本次定向提交；提交前显式开启 TLS 校验获取成功，合并远端返回 Already up to date。
- [x] 完整后端回归及本地合并门禁：原四项运行时失败已修复，最终源码完整复核 1286 通过、63 跳过；真实集成 10 项、合并后 main 专项 286 项通过，审查通过。保留一次 watchdog 耗时断言超限及其后续复核记录，未推送。

2026-09-08：先前自动审批故障已解除。重新执行 `git fetch origin` 成功，`git merge origin/main` 返回 Already up to date。已创建 `.worktrees/skill-version-foundation`（分支 `codex/skill-version-foundation`），安装 Python 3.12.10 独立环境。基线 8 项通过，存在一条既有 Starlette/AnyIO 弃用警告；pip check 通过。已建立专用测试数据库，MinIO 健康检查通过；真实集成测试结果另行记录。

## 准备与基线

恢复审批服务后，先运行 `git fetch origin`，检查 `git log --left-right --oneline HEAD...origin/main`；按用户约定协调合并远端 main。保留主目录所有不相关未跟踪文件，仅显式提交本设计及计划文件。

核实 `.worktrees` 被忽略，再从协调后的 main 创建 `.worktrees/skill-version-foundation`，分支名 `codex/skill-version-foundation`。不存在可直接创建工作区的原生工具时使用 Git；不复用其他任务的工作区。

定位有效 Python 解释器并以绝对路径调用；创建工作区专用虚拟环境，按 `backend/requirements.txt` 安装依赖。不要假设当前 PATH 有 python：上一轮核查未找到该命令。

工作区 backend 目录基线：`python -m pytest tests/test_skills.py tests/collaboration/test_repository.py -q`。记录真实结果；故障定位到环境或既有行为后再开始红绿测试。

## 文件与接口归属

| 文件 | 职责 |
| --- | --- |
| `backend/app/skills/package.py` | 无数据库和网络副作用的 ZIP 校验、清单与摘要 |
| `backend/app/skills/package_storage.py` | Skill 专用对象命名、存储和有界完整性读取 |
| `backend/app/skills/models.py` | Skill、草稿、版本的 SQLAlchemy 模型及不可变约束 |
| `backend/app/skills/repository.py` | 必须带单位/项目的事务性仓储 |
| `backend/app/db/base.py` | 注册新增模型，沿用现有元数据入口 |
| `backend/alembic/versions/20260908_26_skill_versions.py` | 新建表、唯一约束、外键与 PostgreSQL 版本不可变保护 |
| `backend/tests/skills/` | 包、存储和仓储的独立行为测试 |
| `backend/tests/integration/test_skill_versions_postgres.py` | 真实 PostgreSQL 迁移、并发与约束验证 |
| `backend/tests/integration/test_skill_package_minio.py` | 真实对象服务读写及完整性验证 |

迁移父版本在当前基线为 `20260902_25`；获取远端后重新检查 Alembic head，如远端新增迁移则顺接实际 head，避免引入分叉。

### Task 1: 安全文件包解析

**Files:** Create `backend/app/skills/package.py` and `backend/tests/skills/test_package.py`.

**Interfaces:**

```python
@dataclass(frozen=True)
class SkillPackageFile:
    path: str
    data: bytes
    sha256: str

@dataclass(frozen=True)
class ValidatedSkillPackage:
    name: str
    description: str
    display_version: str
    content: str
    files: tuple[SkillPackageFile, ...]
    digest: str

class SkillPackageError(ValueError):
    pass

def parse_skill_bundle(data: bytes) -> tuple[ValidatedSkillPackage, ...]:
    ...
```

以上为待实现接口声明，方法体由下面的行为要求约束。复用 `parse_skill_markdown` 的内容契约，但模块导入不得初始化 SkillService、创建目录或访问数据库。

- [x] **Step 1: 写失败测试。** 使用内存 ZIP 构造器 `make_bundle(entries: list[tuple[str, bytes]]) -> bytes`，支持同名条目而不使用会消除重复键的 dict。测试以下实际行为：

```python
@pytest.mark.parametrize("path", ["../x", "/x", "C:/x", "a/../../x", "a\\..\\x"])
def test_rejects_unsafe_member_paths(path):
    with pytest.raises(SkillPackageError):
        parse_skill_bundle(make_bundle([(path, b"x")]))

def test_rejects_duplicate_normalized_paths():
    entries = [("s/SKILL.md", valid_manifest()), ("s/a.txt", b"a"), ("s/A.txt", b"b")]
    with pytest.raises(SkillPackageError):
        parse_skill_bundle(make_bundle(entries))

def test_digest_ignores_archive_member_order():
    entries = [("s/SKILL.md", valid_manifest()), ("s/ref.txt", b"reference")]
    left = parse_skill_bundle(make_bundle(entries))[0]
    right = parse_skill_bundle(make_bundle(list(reversed(entries))))[0]
    assert left.digest == right.digest
    assert [item.path for item in left.files] == ["SKILL.md", "ref.txt"]
```

测试辅助 `valid_manifest()` 返回 UTF-8 编码的 `---\nname: s\ndescription: Sample\nversion: '1.0'\n---\nInstructions\n`；make_bundle 使用 BytesIO、ZipFile.writestr 和 ZIP_DEFLATED。另外覆盖空包、缺失清单、损坏 CRC、非法 UTF-8、符号链接、特殊文件、嵌套 Skill 根目录、孤立文件、重复 Skill 名称和限额边界。

- [x] **Step 2: 运行红测试。** backend 内执行 `python -m pytest tests/skills/test_package.py -q`；新增模块缺失只能作为初次接口搭建证据，补齐接口后必须观察行为断言失败。
- [x] **Step 3: 实现。** 检查原始名称和规范化名称，拒绝 NUL、绝对路径、驱动器前缀、路径穿越、重复路径及大小写冲突；限制条目总数和声明总字节；按 64 KiB 分块读取，再限制实际累计字节。每个文件恰好属于一个 Skill 根目录，全部包校验通过后才返回。拒绝 Windows 保留名、尾随点/空格和冒号，避免未来沙箱解包的平台差异。
- [x] **Step 4: 定义摘要并跑绿测试。** 按路径排序清单，以 UTF-8、sort_keys=True、separators=(",", ":") 序列化 `{path, size, sha256}` 列表，再计算 SHA-256；路径相对 Skill 根。内容或路径变化必须改变摘要，ZIP 时间戳和成员顺序不影响摘要。运行包测试及原有 `tests/test_skills.py`。
- [x] **Step 5: 审查并提交。** 审查限额绕过、无文件副作用和原有接口兼容，定向暂存两个文件，提交 `feat: validate immutable skill packages`。此任务不接管旧 ZIP 路由。

### Task 2: 不可变对象保存与有界读取

**Files:** Create `backend/app/skills/package_storage.py`, `backend/tests/skills/test_package_storage.py`, `backend/tests/integration/test_skill_package_minio.py`.

**Interfaces:** 使用 Task 1 类型；`SkillPackageStorage(client, bucket)` 接收 Boto3 S3 客户端和专用测试或业务桶。`put(unit_id, project_id, skill_id, package) -> StoredSkillPackage` 返回 object_key、archive_sha256、package_digest、size_bytes。`read(stored) -> bytes` 校验传输实际长度与 archive_sha256，发生错误抛 `SkillPackageStorageError`。

StoredSkillPackage 是冻结数据类，字段为 `object_key: str`、`archive_sha256: str`、`package_digest: str`、`size_bytes: int`。默认业务桶通过 `IAP_SKILL_BUCKET` 配置为 `iap-skills`，不更改 Artifact 桶。

- [x] **Step 1: 写失败测试。** 内存 S3 替身仅实现 put_object/get_object，真实保存和读取字节；测试校验包内容、两次 put 产生不同对象键、损坏正文拒绝、声明长度与实长不一致拒绝、超限流停止读取并关闭流。

```python
def test_round_trips_verified_package(memory_s3):
    storage = SkillPackageStorage(memory_s3, "test-skills")
    package = parse_skill_bundle(make_bundle([("s/SKILL.md", valid_manifest())]))[0]
    stored = storage.put("unit", "project", "skill", package)
    restored = parse_skill_bundle(storage.read(stored))[0]
    assert restored.digest == package.digest

def test_two_uploads_do_not_overwrite(memory_s3, package):
    storage = SkillPackageStorage(memory_s3, "test-skills")
    first = storage.put("unit", "project", "skill", package)
    second = storage.put("unit", "project", "skill", package)
    assert first.object_key != second.object_key
    assert storage.read(first) == storage.read(second)
```

package fixture 调用 Task 1 的内存 ZIP 辅助函数生成自包含正文包；memory_s3 只模拟网络边界，不模拟摘要或解析函数。

- [x] **Step 2: 运行红测试。** `python -m pytest tests/skills/test_package_storage.py -q`，确认针对完整性和覆盖行为的断言失败。
- [x] **Step 3: 实现。** 生成固定时间戳、路径排序的 ZIP，超过 10 MiB 编码包上限时拒绝；object_key 的所有作用域段先由服务端验证，再使用随机 UUID 存储尝试标识。不要使用调用者文件名作为对象路径。Boto3 设置有限连接/读取超时和重试次数。读取按块限制 10 MiB，并在 finally 中关闭 StreamingBody。
- [x] **Step 4: 跑绿测试与 MinIO 验证。** `python -m pytest tests/skills/test_package.py tests/skills/test_package_storage.py -q`。集成测试显式读取 `TEST_S3_ENDPOINT`、`TEST_S3_ACCESS_KEY`、`TEST_S3_SECRET_KEY`，只使用本次 UUID 测试桶，真实读回并验证摘要，测试结束只删除自己创建的对象和桶。缺少配置标记 skipped，并记录未验证。
- [x] **Step 5: 审查并提交。** 确认不输出凭据、不提供任意路径读取、不覆盖其他对象；提交 `feat: store verified skill version packages`。

### Task 3: 项目范围内的草稿与发布版本仓储

**Files:** Create `backend/app/skills/models.py`, `backend/app/skills/repository.py`, `backend/alembic/versions/20260908_26_skill_versions.py`, `backend/tests/skills/test_repository.py`, `backend/tests/integration/test_skill_versions_postgres.py`; modify `backend/app/db/base.py`.

**Interfaces:** `SkillRepository(session: Session)` 不隐式 commit。新增 `SkillScope(unit_id: str, project_id: str)` 冻结数据类，所有公开方法首参必须为 scope。公开操作为：

```python
create(scope, *, skill_id: str, name, created_by, package, stored) -> Skill
get(scope, skill_id) -> Skill | None
list(scope, *, offset=0, limit=20) -> list[Skill]
save_draft(scope, skill_id, *, expected_revision, package, stored) -> SkillDraft
publish(scope, skill_id, *, expected_revision, idempotency_key, published_by) -> SkillVersion
get_version(scope, skill_id, version_id) -> SkillVersion | None
```

package 和 stored 分别为 Tasks 1/2 类型；发布需当前 stored 的摘要及正文已由上层重新验证。调用者在上传前预分配服务端 UUID，再将同一 skill_id 交给存储和 create；仓储验证 UUID、对象路径作用域以及包摘要一致性。仓储不代表授权，后续服务层仍需验证 skill.manage/invoke。错误类型 `SkillRevisionConflict`、`SkillIdempotencyConflict`、`SkillResourceNotFound` 均在 repository.py 定义。

- [x] **Step 1: 写失败测试。** 独立 Session 中验证不同项目允许同名、同项目重复拒绝、跨作用域 get/list/version 均不可见、陈旧草稿 revision 拒绝、发布内容在修改草稿后不变、同键同请求重放返回同一版本、同键不同请求拒绝、跨 Skill 的版本引用被数据库拒绝。

```python
def test_publish_replay_keeps_same_version(repository, scope, draft):
    first = repository.publish(scope, draft.skill_id, expected_revision=1,
                               idempotency_key="publish-1", published_by="editor")
    replay = repository.publish(scope, draft.skill_id, expected_revision=1,
                                idempotency_key="publish-1", published_by="editor")
    assert replay.id == first.id
    assert replay.version == 1
```

repository fixture 使用真实 SQLAlchemy Session；scope 使用不同 UUID 的单位/项目测试数据；draft 通过 create 和 Tasks 1/2 的测试包创建，不手工构造与约束不符的 ORM 行。SQLite 只用于快速仓储行为检查。

- [x] **Step 2: 运行红测试。** `python -m pytest tests/skills/test_repository.py -q`；先观察项目隔离、revision 和版本重放的行为失败。
- [x] **Step 3: 实现模型与事务。** 建 skills、skill_drafts、skill_versions；加唯一 `(unit_id, project_id, name)`、`(skill_id, version)`、`(skill_id, idempotency_key)`；发布指针用包含 skill_id 的复合外键保证归属。草稿更新使用 revision 比较交换。publish 先查询已有幂等记录，再锁 Skill 行，验证草稿 revision，复制完整正文与文件清单到新版本并更新发布指针。请求摘要包含 skill_id、expected_revision、调用者和操作，不使用重试时变化的当前草稿计算已有请求的摘要。
- [x] **Step 4: 实现不可变保护与集成测试。** 应用 ORM 防护与 PostgreSQL UPDATE/DELETE 触发器共同保护 skill_versions，直接 SQL 更新/删除也必须失败。事务回滚必须同时撤销版本和发布指针。用两个独立数据库连接及同步屏障测试并发发布，确认版本唯一；相同 key 获得同一版本，不同 key 使用陈旧 revision 时 409 语义。首次发布消耗 revision，避免第二个请求重复发布同一修订。
- [x] **Step 5: 检查迁移与失败回滚。** 独立 `TEST_DATABASE_URL` 上执行迁移、集成测试和约束查询；含业务数据的环境禁止 downgrade。测试库可在测试完成后按依赖顺序回收本次 fixture。新增模块注册后运行既有模型与 Team 仓储测试，确认无循环导入。
- [x] **Step 6: 审查并提交。** `python -m pytest tests/skills tests/collaboration/test_repository.py tests/test_skills.py -q`；真实 PostgreSQL 集成必须单独记录。定向提交 `feat: persist scoped skill drafts and immutable versions`。

## 子计划验收与后续接入

基础子计划验收要求 Tasks 1-3 单元测试通过，真实 PostgreSQL 和 MinIO 集成通过，并完成代码审查。单独记录任何未满足条件，不以新增代码文件数或 skipped 测试宣称验收成功。

本计划完成后仍不代表 Skill 生产化阶段完成。后续子计划依次为：

1. 权限与控制面：认证依赖、skill.read/manage/invoke、project_admin 权限映射、懒加载服务、原子导入、发布审计、分页 API 和错误契约。
2. 运行接入：Agent/Team 确定版本绑定、消息接受事务内捕获 Skill、快照格式升级、模型/工具/恢复边界重新授权、正文预算与单 Agent 正文注入。
3. 管理界面：项目切换、列表分页、草稿及发布、不可变版本、文件和引用详情、Agent 绑定版本及升级提示。
4. 受限参考文件：Gateway 校验清单路径、实时权限、摘要和额度，返回有界文本。
5. 迁移与切换：只读盘点、显式项目映射、幂等草稿导入、旧 Team 重新发布、在途运行兼容和回滚演练。

后续计划须在各自实施前给出逐文件步骤及接口，不在基础模块未验证时并行切换生产调用链。Task 3 的 schema 和事务结果是控制面计划的输入；暂未实现的新模块不能通过主应用路由访问。

## 自检记录

- 已覆盖整体设计第 4 节的包/版本基础和第 7 节的 ZIP 安全；授权 API、运行时与迁移明确归属后续子计划。
- 现有 `S3ObjectStorage.get_bytes` 是无上限读取，故 Skill 适配器使用有界流读取，不改动共享 Artifact 行为。
- 新版本包含正文与文件清单，后续 Team 不必读取当前可变文件目录。
- 幂等重试检查在 revision 检查之前；正常新版发布消耗草稿 revision。
- 工作区、Tasks 1-3 红绿测试、真实集成及逐任务审查均已执行。原四项运行时失败已修复，最终源码完整回归及合并后专项验证通过；一次 watchdog 计时失败、后续复核和剩余风险详见 [验收记录](2026-09-08-skill-foundation-verification.md)。基础子计划完成，不代表公开 Skill API、运行时版本绑定和界面已经交付。
