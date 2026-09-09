# Project Skill Production Activation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在默认关闭且可快速回退的前提下，把已验收的 `/api/project-skills` 挂载到生产 FastAPI 应用，并交付显式迁移、Skill 桶初始化、启动校验和真实主应用验收。

**Architecture:** 通过 `IAP_PROJECT_SKILLS_API_ENABLED` 条件挂载现有路由；开关开启时在后台调度器启动前精确比较代码和数据库 Alembic heads。API 镜像只启动 Uvicorn，数据库迁移和桶初始化由两个独立 Compose 运维任务执行；Project Skill 的验证错误使用路径限定的安全 422 处理器，其他 API 契约不变。

**Tech Stack:** Python 3.12、FastAPI、Pydantic 2、SQLAlchemy 2、Alembic、PostgreSQL 16、Boto3/MinIO、Docker Compose、PyYAML、pytest；不新增依赖。

## Global Constraints

- 依据已确认设计 `docs/superpowers/specs/2026-09-09-project-skill-production-activation-design.md`，设计提交 `51d3fca`。
- 执行时继续使用独立 worktree 和分支 `codex/project-skill-production-activation`；不得在 main 直接写生产代码。
- 新 `/api/project-skills` 默认关闭；只有 `IAP_PROJECT_SKILLS_API_ENABLED=true` 时才挂载并执行专用数据库启动校验。
- 旧 `/api/skills` 始终保留；不修改其 router/service，不做旧目录数据迁移、双写或自动回填。
- 不修改前端、Agent、Team、运行时、知识库、Embedding、GIS 或脚本执行行为。
- 数据库迁移只能通过显式运维任务执行；API 启动不得运行 Alembic、SQLite 导入或桶创建。
- 开关开启时，数据库 `alembic_version` 集合必须与镜像代码迁移图的全部 heads 精确相等；失败信息不得包含数据库 URL、主机凭据或底层异常文本。
- MinIO 不作为全局 API 启动门槛；桶初始化失败必须阻止功能启用，运行期存储错误仍使用 `skill_storage_unavailable`/503。
- Project Skill 的孤立 Unicode surrogate 请求必须返回可编码的 422 且无数据库、审计或对象副作用；其他 API 的默认验证响应逐字段保持不变。
- 回退只关闭开关并重启 API；不 downgrade、不删除表、桶或不可变版本。
- 所有破坏性数据库测试只允许使用新建专用库 `iap_project_skill_activation_test_20260909_a` 和 `iap_project_skill_activation_migration_20260909_a`。连接后还要用 `current_database()` 二次确认；业务库 `iap` 禁止用于测试重置或降级。
- 真实 MinIO 测试只创建 UUID 桶并删除自己创建的对象/桶；不枚举、修改或清理共享桶。
- 每项按行为 RED → 最小实现 → GREEN → 定向审查 → 提交。任何运行时计时失败先隔离诊断，不放宽断言或并发运行计时套件。
- 每次提交前使用 TLS 验证获取 `origin/main`，合并后重跑受影响测试；只暂存该任务文件。不得修改用户全局 Git 配置。
- 不推送、不部署远程服务器，除非用户在最终分支收尾时重新选择。

---

## Status and Execution Environment

- [x] 用户确认生产后端 API 挂载范围。
- [x] 用户确认新旧 Skill API 并行。
- [x] 用户确认默认关闭的配置开关。
- [x] 用户确认数据库版本不完整时拒绝启动，MinIO 不作为全局启动门槛。
- [x] 用户确认显式迁移、独立桶初始化和 Unicode 422 修复。
- [x] 书面设计已提交并获用户确认。
- [ ] Task 1：配置开关与生产路由边界。
- [ ] Task 2：数据库 revision 启动校验。
- [ ] Task 3：Project Skill 验证错误安全序列化。
- [ ] Task 4：API 启动与显式迁移任务分离。
- [ ] Task 5：幂等 Skill 桶初始化任务。
- [ ] Task 6：真实生产主应用和容器化发布演练。
- [ ] 全分支审查、完整回归和交付记录。

执行前用 `superpowers:using-git-worktrees` 核对环境。可将现有、已合并且 tracked-clean 的 `.worktrees/skill-version-foundation` 切换为新分支以复用其 `.venv`；若其中出现用户改动，则创建 `.worktrees/project-skill-production-activation`，并使用前者的 Python 解释器但把 `PYTHONPATH` 指向新 worktree 的 `backend`。不得删除旧 worktree 中用户文件。

计划控制端创建专属忽略目录 `.superpowers/sdd/2026-09-09-project-skill-production-activation/`，保存 ledger、任务 brief、review package 和验证辅助脚本。普通测试清除所有 `TEST_*`；service 模式将 `DATABASE_URL`/`TEST_DATABASE_URL` 指向专用服务库并注入 UUID MinIO 桶配置；migration 模式只指向专用迁移库。辅助脚本从 Docker inspect 读取本机测试容器凭据到进程内存，使用 `finally` 恢复环境，不打印或写入凭据。

创建专用数据库前只读查询 `pg_database`。若任一计划数据库已存在但不是本计划 ledger 记录的资源，改用唯一后缀并同时更新 ledger、测试常量和验收报告；禁止覆盖。两个数据库分别用于服务集成和 26→head 启动校验，不能互换。

## File Map

| File | Responsibility |
| --- | --- |
| `backend/app/core/settings.py` | 读取默认关闭的 Project Skill API 开关 |
| `backend/app/main.py` | 条件挂载、启动校验顺序和路径限定验证错误处理器注册 |
| `backend/app/skills/project_startup.py` | 读取代码/数据库 Alembic heads 并执行凭据安全的启动校验 |
| `backend/app/skills/project_validation.py` | 仅对 Project Skill 验证错误递归生成 UTF-8 安全内容 |
| `backend/app/skills/package_storage.py` | 公开单一的 Skill 存储配置和客户端工厂，供运行时及桶初始化共用 |
| `backend/app/skills/storage_bootstrap.py` | 幂等创建并确认 Skill 桶的 CLI 边界 |
| `backend/Dockerfile` | API 默认命令只启动 Uvicorn |
| `compose.yaml` | API 配置、显式 migrate 和 skill-storage-init 运维服务 |
| `.env.example` | 默认关闭开关和 `IAP_SKILL_BUCKET` 示例 |
| `backend/README.md` | 本地和 Compose 运维命令、启用及回退顺序 |
| `docs/deployment/project-skill-production-activation.md` | 不含秘密的生产发布/回退 runbook |
| `backend/tests/core/test_settings.py` | 开关解析与兼容性 |
| `backend/tests/test_main.py` | 默认/启用路由和 lifespan 调用顺序 |
| `backend/tests/skills/test_project_startup.py` | Alembic head 读取、比较和安全失败 |
| `backend/tests/skills/test_project_validation.py` | surrogate 422、零副作用和非目标路径兼容 |
| `backend/tests/skills/test_storage_bootstrap.py` | fake S3 幂等/错误矩阵与 CLI 输出安全 |
| `backend/tests/test_project_skill_deployment.py` | Dockerfile、Compose 和 env 的结构化边界 |
| `backend/tests/integration/test_project_skill_startup_postgres.py` | 真实 PostgreSQL 26→head 启动校验 |
| `backend/tests/integration/test_project_skill_storage_bootstrap_minio.py` | 真实 MinIO 桶初始化两次和自有资源清理 |
| `backend/tests/integration/test_project_skill_main_app.py` | 启用后的真实 `app.main` Cookie/CSRF/Origin/API 契约 |
| `docs/superpowers/plans/2026-09-09-project-skill-production-activation-verification.md` | 最终命令、结果、skip、警告、范围和限制 |

## Task 1: Configuration Gate and Production Route Boundary

**Files:**
- Modify: `backend/app/core/settings.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/core/test_settings.py`
- Modify: `backend/tests/test_main.py`
- Verify unchanged: `backend/app/skills/router.py`
- Verify unchanged: `backend/app/skills/service.py`

**Interfaces:**
- Consumes: existing `_read_bool(name: str, default: bool) -> bool`, `settings`, and `app.skills.project_router.router`.
- Produces: `Settings.project_skills_api_enabled: bool`; production route mounting controlled only by that field.

- [ ] **Step 1: Write configuration RED tests.**

Add exact assertions to `backend/tests/core/test_settings.py`:

```python
def test_project_skills_api_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("IAP_PROJECT_SKILLS_API_ENABLED", raising=False)
    assert Settings.from_env().project_skills_api_enabled is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "TRUE"])
def test_project_skills_api_accepts_supported_true_values(monkeypatch, value):
    monkeypatch.setenv("IAP_PROJECT_SKILLS_API_ENABLED", value)
    assert Settings.from_env().project_skills_api_enabled is True
```

Also instantiate `Settings` once with its existing required named arguments and without the new argument; assert the field remains `False`. This protects backward compatibility for explicit constructors.

- [ ] **Step 2: Write actual main route RED tests.**

In `backend/tests/test_main.py`, preserve the current default app import and add:

```python
PROJECT_METHODS = {
    "/api/project-skills": {"get", "post"},
    "/api/project-skills/import": {"post"},
    "/api/project-skills/{skill_id}": {"get"},
    "/api/project-skills/{skill_id}/draft": {"get", "put"},
    "/api/project-skills/{skill_id}/publish": {"post"},
    "/api/project-skills/{skill_id}/versions": {"get"},
    "/api/project-skills/{skill_id}/versions/{version_id}": {"get"},
}


def test_project_skill_routes_are_absent_by_default():
    paths = app.openapi()["paths"]
    assert not any(path.startswith("/api/project-skills") for path in paths)
    assert "/api/skills" in paths


def test_project_skill_request_is_an_ordinary_404_by_default():
    response = TestClient(app).get("/api/project-skills")
    assert response.status_code == 404
```

Add a subprocess helper that starts a fresh interpreter with `PYTHONPATH=<repo>/backend` and `IAP_PROJECT_SKILLS_API_ENABLED=true`, imports `app.main.app`, serializes only its OpenAPI path/method map to JSON, and asserts the map above plus `/api/skills`. Do not reload `app.main` in the pytest process because its global settings, engine and scheduler are shared with other tests.

- [ ] **Step 3: Run RED and record only behavioral failures.**

Run:

```powershell
python -m pytest backend/tests/core/test_settings.py backend/tests/test_main.py -q -p no:cacheprovider --tb=short
```

Expected: missing `project_skills_api_enabled` and enabled subprocess missing `/api/project-skills`; existing tests remain green.

- [ ] **Step 4: Implement the minimal gate.**

Append the optional field after existing non-default dataclass fields:

```python
project_skills_api_enabled: bool = False
```

Populate it in `Settings.from_env()` with:

```python
project_skills_api_enabled=_read_bool("IAP_PROJECT_SKILLS_API_ENABLED", False),
```

Import the existing project router under an unambiguous alias and mount it without an extra prefix:

```python
from .skills.project_router import router as project_skills_router

if settings.project_skills_api_enabled:
    app.include_router(project_skills_router)
```

Do not change the project router's own `/api/project-skills` prefix and do not conditionally remove the old router.

- [ ] **Step 5: Run GREEN and protected-scope checks.**

Run the RED command again, then:

```powershell
python -m pytest backend/tests/skills/test_project_api.py backend/tests/test_skills.py -q -p no:cacheprovider --tb=short
git diff --exit-code HEAD -- backend/app/skills/router.py backend/app/skills/service.py frontend backend/app/agents backend/app/collaboration backend/app/runtime
```

Expected: all selected tests pass; protected paths have no diff.

- [ ] **Step 6: Fetch, merge, verify and commit only Task 1.**

Use the approved secure fetch override, merge `origin/main`, rerun Step 5, then stage only the four Task 1 files and commit:

```text
feat: gate project skill production routes
```

## Task 2: Database Revision Startup Gate

**Files:**
- Create: `backend/app/skills/project_startup.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/skills/test_project_startup.py`
- Modify: `backend/tests/test_main.py`
- Create: `backend/tests/integration/test_project_skill_startup_postgres.py`

**Interfaces:**
- Consumes: `SessionFactory`, `settings.project_skills_api_enabled`, `backend/alembic.ini`, and `backend/alembic/`.
- Produces: `ProjectSkillStartupError`; `load_code_migration_heads(config_path: Path | None = None) -> frozenset[str]`; `validate_project_skills_startup(enabled: bool, session_factory: Callable[[], Session]) -> None`.

- [ ] **Step 1: Write migration-head reader RED tests.**

Create `test_project_startup.py` with a repository-layout behavior test:

```python
def test_loads_repository_migration_heads_without_using_current_directory(
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(tmp_path)
    heads = load_code_migration_heads()
    assert heads
    assert all(head.isascii() and head.strip() == head for head in heads)
```

This catches an implementation that resolves `alembic.ini` from the process working directory while avoiding a test that must be edited whenever a legitimate migration advances the repository head. Use temporary Alembic directories to cover an empty/broken graph and a synthetic two-head graph with hand-written literal revision identifiers. Graph-read failures must raise `ProjectSkillStartupError("project skill migration graph is unavailable")` with no retained cause/context.

- [ ] **Step 2: Write database comparison RED tests.**

Use an in-memory SQLite session factory and create only `alembic_version(version_num VARCHAR(32) NOT NULL)`. Cover disabled/no database access, exact match, database behind, database ahead, two database heads, missing table and a factory that raises `RuntimeError("postgresql://user:startup-secret@host/db")`.

Assertions for the secret failure must walk the complete exception chain and verify `startup-secret` is absent, `__cause__ is None`, and `__context__ is None`. Mismatch messages may contain only sorted revision identifiers.

- [ ] **Step 3: Run unit RED.**

```powershell
python -m pytest backend/tests/skills/test_project_startup.py -q -p no:cacheprovider --tb=short
```

Expected: import failure for the new module.

- [ ] **Step 4: Implement the narrow validator.**

Use the following public shape:

```python
class ProjectSkillStartupError(RuntimeError):
    pass


def load_code_migration_heads(config_path: Path | None = None) -> frozenset[str]:
    path = config_path or Path(__file__).resolve().parents[2] / "alembic.ini"
    try:
        heads = frozenset(ScriptDirectory.from_config(Config(str(path))).get_heads())
    except Exception:
        heads = frozenset()
    if not heads:
        raise ProjectSkillStartupError(
            "project skill migration graph is unavailable"
        ) from None
    return heads


def _load_database_migration_heads(
    session_factory: Callable[[], Session],
) -> frozenset[str] | None:
    try:
        with session_factory() as session:
            return frozenset(
                session.scalars(text("SELECT version_num FROM alembic_version"))
            )
    except Exception:
        return None


def validate_project_skills_startup(
    enabled: bool,
    session_factory: Callable[[], Session],
) -> None:
    if not enabled:
        return
    expected = load_code_migration_heads()
    actual = _load_database_migration_heads(session_factory)
    if actual is None:
        raise ProjectSkillStartupError(
            "project skill database revision is unavailable"
        ) from None
    if actual != expected:
        raise ProjectSkillStartupError(
            f"project skill database revision mismatch: "
            f"expected={sorted(expected)!r}, actual={sorted(actual)!r}"
        )
```

The broad catches are permitted only at this process-start boundary and must replace, not chain, secret-bearing infrastructure errors. Never raise the public wrapper from inside an `except` block: `from None` suppresses display but leaves `__context__` attached. Convert the caught failure to an empty/sentinel return first, then raise after leaving the handler so `__cause__` and `__context__` are both `None`.

- [ ] **Step 5: Integrate lifespan ordering and write its RED/GREEN test.**

Call before scheduler startup:

```python
validate_runner_gateway_startup()
validate_project_skills_startup(
    settings.project_skills_api_enabled,
    SessionFactory,
)
default_mcp_health_scheduler.start()
```

In `test_main.py`, monkeypatch both validators and scheduler methods. Assert order `runner`, `project`, `scheduler`; then make the project validator raise and assert scheduler `start` was never called.

- [ ] **Step 6: Write and run the real PostgreSQL migration gate.**

The new integration file must reject every URL except PostgreSQL database `iap_project_skill_activation_migration_20260909_a` with no query parameters, confirm `current_database()` before `DROP SCHEMA public CASCADE`, and never disable immutable triggers.

For each case reset only that schema, run Alembic through `sys.executable` with `DATABASE_URL=TEST_DATABASE_URL`, then:

```python
upgrade("20260908_26")
with pytest.raises(ProjectSkillStartupError, match="revision mismatch"):
    validate_project_skills_startup(True, factory)
upgrade("head")
validate_project_skills_startup(True, factory)
```

Run once without configuration to prove an ordinary skip, then through the plan helper's migration mode and require zero skips.

- [ ] **Step 7: Run Task 2 GREEN and commit.**

Run unit startup, main, migration graph and real dedicated migration tests. Secure-fetch/merge, rerun affected suites, stage only Task 2 files and commit:

```text
feat: validate project skill database at startup
```

## Task 3: UTF-8-Safe Project Skill Validation Errors

**Files:**
- Create: `backend/app/skills/project_validation.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/skills/test_project_validation.py`
- Modify: `backend/tests/test_main.py`

**Interfaces:**
- Consumes: FastAPI `RequestValidationError`, default `request_validation_exception_handler`, `jsonable_encoder`, and Starlette `JSONResponse`.
- Produces: `project_skill_validation_exception_handler(request: Request, error: RequestValidationError) -> Response`.

- [ ] **Step 1: Recover the exact surrogate RED.**

Build an isolated FastAPI app using `make_test_app(sessions, lambda: storage, context=make_context("skill.manage"))`, register the new handler, and send exact bytes:

```python
response = client.post(
    "/api/project-skills",
    content=b'{"content":"\\ud800"}',
    headers={"Content-Type": "application/json"},
)
```

Assert the pre-fix response is 500 with `raise_server_exceptions=False`, then add final expectations: status 422, JSON root `detail`, no `Skill`, `SkillDraft` or `AuditEvent` rows, and `memory_s3.objects == {}`. Record the behavioral RED before implementation.

- [ ] **Step 2: Write non-target compatibility RED tests.**

Create otherwise identical apps with an integer path/body validation failure. Register the wrapper only on one app and assert its non-Project response status, JSON and content type equal FastAPI's default response. Also assert `/api/project-skills-other` delegates to the default handler; prefix matching must be exact root or root plus `/`.

- [ ] **Step 3: Implement recursive sanitation and handler.**

Use these exact boundaries:

```python
PROJECT_SKILL_ROOT = "/api/project-skills"


def _is_project_skill_path(path: str) -> bool:
    return path == PROJECT_SKILL_ROOT or path.startswith(f"{PROJECT_SKILL_ROOT}/")


def utf8_safe(value: Any) -> Any:
    if isinstance(value, str):
        return value.encode("utf-8", "replace").decode("utf-8")
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, dict):
        return {
            utf8_safe(key) if isinstance(key, (str, bytes)) else key: utf8_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [utf8_safe(item) for item in value]
    return value


async def project_skill_validation_exception_handler(request, error):
    if not _is_project_skill_path(request.url.path):
        return await request_validation_exception_handler(request, error)
    content = jsonable_encoder(utf8_safe({"detail": error.errors()}))
    return JSONResponse(status_code=422, content=content)
```

Do not catch service exceptions or remove validation fields.

- [ ] **Step 4: Register only with the enabled production route.**

Inside the same `if settings.project_skills_api_enabled` block that mounts the router, call:

```python
app.add_exception_handler(
    RequestValidationError,
    project_skill_validation_exception_handler,
)
```

Extend the enabled-main subprocess with an actual HTTP probe through the production `app` object. In the subprocess only, override the existing request-context/session dependencies with the same narrow fixtures used by Project Skill API tests, send the exact surrogate request bytes to the real `/api/project-skills` route, and assert 422 with a decodable `detail` payload. Do not inspect `app.exception_handlers` or assert the registered callable's identity. The default main must still omit the new route.

- [ ] **Step 5: Run GREEN, compatibility regression and commit.**

Run `test_project_validation.py`, `test_main.py`, existing Project Skill API/Cookie tests, and representative non-project API validation tests. Secure-fetch/merge, rerun, stage only Task 3 files and commit:

```text
fix: serialize project skill validation errors safely
```

## Task 4: Explicit Database Migration Operation

**Files:**
- Modify: `backend/Dockerfile`
- Modify: `compose.yaml`
- Modify: `.env.example`
- Create: `backend/tests/test_project_skill_deployment.py`
- Modify: `backend/README.md`
- Create: `docs/deployment/project-skill-production-activation.md`

**Interfaces:**
- Consumes: current API image contents, `python -m alembic`, `python -m app.migrations.sqlite_to_postgres`.
- Produces: image `intelligent-agent-platform-api:local`; Compose `migrate` service in profile `operations`; Uvicorn-only API default command.

- [ ] **Step 1: Write structured deployment RED tests.**

Parse `compose.yaml` with `yaml.safe_load`; parse `.env.example` into key/value entries. Assert:

```python
migrate = compose["services"]["migrate"]
assert migrate["profiles"] == ["operations"]
assert migrate["restart"] == "no"
assert migrate["image"] == compose["services"]["api"]["image"]
assert migrate["command"] == [
    "sh", "-c",
    "python -m alembic upgrade head && exec python -m app.migrations.sqlite_to_postgres",
]
assert "migrate" not in compose["services"]["api"].get("depends_on", {})
```

Also assert API receives `${IAP_PROJECT_SKILLS_API_ENABLED:-false}`, migrate receives only required database/legacy paths, and `.env.example` defaults the flag to `false`.

- [ ] **Step 2: Run RED.**

```powershell
python -m pytest backend/tests/test_project_skill_deployment.py -q -p no:cacheprovider --tb=short
```

Expected: Compose lacks `migrate`, API lacks the feature environment entry, and the env example lacks the disabled feature flag.

- [ ] **Step 3: Separate the image command and Compose service.**

Set Dockerfile default command exactly to Uvicorn JSON exec form with existing host/port/proxy behavior:

```dockerfile
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
```

Give API `image: intelligent-agent-platform-api:local`. Add the profile service:

```yaml
migrate:
  profiles: ["operations"]
  image: intelligent-agent-platform-api:local
  command:
    - sh
    - -c
    - python -m alembic upgrade head && exec python -m app.migrations.sqlite_to_postgres
  environment:
    DATABASE_URL: "${DATABASE_URL:-postgresql+psycopg://iap:iap@postgres:5432/iap}"
    LEGACY_SQLITE_DATA_DIR: "${LEGACY_SQLITE_DATA_DIR:-/data}"
  depends_on:
    postgres:
      condition: service_healthy
  volumes:
    - model-provider-data:/data
  restart: "no"
```

API continues depending only on its current infrastructure services, never on operations-profile jobs.

- [ ] **Step 4: Document exact migration and rollback commands.**

The runbook must say: build/tag API; leave feature false; run `docker compose --profile operations run --rm migrate`; inspect exit code; only then proceed. It must warn that ordinary `docker compose up` no longer migrates, multi-instance API containers must not run Alembic, and rollback never downgrades.

- [ ] **Step 5: Run GREEN and Compose/image checks.**

Run the RED test, existing Compose boundary tests and `docker compose config`, then build the API image. Inspect the built image through `docker image inspect --format '{{json .Config.Cmd}}' intelligent-agent-platform-api:local`; parse the returned JSON and require the exact Uvicorn argv with no shell migration chain. This image behavior check, rather than a Dockerfile source-text assertion, is the Docker command acceptance gate.

- [ ] **Step 6: Execute migrate against only the dedicated service database.**

Create `iap_project_skill_activation_test_20260909_a` after exact-name absence confirmation. Run the Compose migrate service with a container-network URL targeting that database and an empty plan-owned legacy directory. Query `alembic_version` and require the current code head; run migrate a second time and require the same revision and zero failure. Never use `iap`.

- [ ] **Step 7: Secure-fetch/merge, rerun and commit Task 4.**

Stage only the six Task 4 files and commit:

```text
ops: separate api startup from database migration
```

## Task 5: Idempotent Skill Bucket Initialization

**Files:**
- Modify: `backend/app/skills/package_storage.py`
- Create: `backend/app/skills/storage_bootstrap.py`
- Create: `backend/tests/skills/test_storage_bootstrap.py`
- Create: `backend/tests/integration/test_project_skill_storage_bootstrap_minio.py`
- Modify: `backend/tests/skills/test_package_storage.py`
- Modify: `backend/tests/test_project_skill_deployment.py`
- Modify: `compose.yaml`
- Modify: `.env.example`
- Modify: `backend/README.md`
- Modify: `docs/deployment/project-skill-production-activation.md`

**Interfaces:**
- Consumes: existing `STORAGE_CLIENT_CONFIG`, Boto3 S3 client and `IAP_OBJECT_STORAGE_*`/`IAP_SKILL_BUCKET` variables.
- Produces: frozen `SkillStorageSettings`; `load_skill_storage_settings() -> SkillStorageSettings`; `create_skill_storage_client(config: SkillStorageSettings) -> Any`; `ensure_skill_bucket(client: Any, bucket: str, region: str) -> None`; CLI `python -m app.skills.storage_bootstrap`.

- [ ] **Step 1: Write shared configuration RED tests.**

Extend `test_package_storage.py` to assert one frozen configuration object supplies endpoint, access key, secret, region and bucket to both client creation and `SkillPackageStorage`. Assert `repr(config)` excludes test access/secret values and existing default/custom factory behavior is unchanged.

- [ ] **Step 2: Write fake S3 bootstrap RED matrix.**

Create a fake client that records `head_bucket` and `create_bucket`. Use real `botocore.exceptions.ClientError` values to cover:

- accessible existing bucket: one HEAD, zero CREATE;
- 404/NoSuchBucket: CREATE then HEAD;
- create `BucketAlreadyOwnedByYou`: retry HEAD and succeed;
- `BucketAlreadyExists`, AccessDenied, endpoint/network exception: stable `SkillBucketBootstrapError`, no credential in exception chain;
- non-`us-east-1`: exact `CreateBucketConfiguration.LocationConstraint`;
- CLI success exit 0 and failure exit 1 with one stable stderr line and no traceback/secret.

- [ ] **Step 3: Run RED.**

```powershell
python -m pytest backend/tests/skills/test_package_storage.py backend/tests/skills/test_storage_bootstrap.py -q -p no:cacheprovider --tb=short
```

Expected: missing shared settings and bootstrap module.

- [ ] **Step 4: Implement the shared storage factory.**

Use a frozen secret-redacting value:

```python
@dataclass(frozen=True)
class SkillStorageSettings:
    endpoint_url: str
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    region_name: str
    bucket: str
```

`load_skill_storage_settings()` reads the same defaults currently embedded in `create_default_skill_package_storage`. `create_skill_storage_client(config)` is the only location calling `boto3.client("s3", ...)`. The existing default storage factory composes these two functions and returns `SkillPackageStorage(client, config.bucket)`.

- [ ] **Step 5: Implement the idempotent bootstrap boundary.**

Define missing codes `{404, "404", "NoSuchBucket", "NotFound"}` and owned-race code `BucketAlreadyOwnedByYou`. Create requests use only `Bucket` for `us-east-1`; other regions also include `CreateBucketConfiguration={"LocationConstraint": region}`. After every create or owned race, require a successful HEAD.

Wrap all unapproved failures as `SkillBucketBootstrapError("unable to initialize project skill bucket") from None`. As in Task 2, the client-call helper must translate caught exceptions to a private status/sentinel and the public boundary must raise only after leaving the `except` block, so secret-bearing `ClientError`/endpoint exceptions are absent from both `__cause__` and `__context__`. `main()` catches only `SkillBucketBootstrapError`, writes the stable message to stderr and raises `SystemExit(1) from None`; success produces no credential/config output.

- [ ] **Step 6: Add the Compose operation and deployment assertions.**

Task 4 already owns the API feature-flag entry. In this task, add the API environment entry for `IAP_SKILL_BUCKET`, add `IAP_SKILL_BUCKET=iap-skills` to `.env.example`, and add:

```yaml
skill-storage-init:
  profiles: ["operations"]
  image: intelligent-agent-platform-api:local
  command: ["python", "-m", "app.skills.storage_bootstrap"]
  environment:
    IAP_OBJECT_STORAGE_ENDPOINT: "${IAP_OBJECT_STORAGE_ENDPOINT:-http://minio:9000}"
    IAP_OBJECT_STORAGE_ACCESS_KEY: "${IAP_OBJECT_STORAGE_ACCESS_KEY:-iap-access}"
    IAP_OBJECT_STORAGE_SECRET_KEY: "${IAP_OBJECT_STORAGE_SECRET_KEY:-change-me-minio-secret}"
    IAP_OBJECT_STORAGE_REGION: "${IAP_OBJECT_STORAGE_REGION:-us-east-1}"
    IAP_SKILL_BUCKET: "${IAP_SKILL_BUCKET:-iap-skills}"
  depends_on:
    minio:
      condition: service_healthy
  restart: "no"
```

API must not depend on this job. `IAP_SKILL_BUCKET` is added to `.env.example` only in this task; Task 4 must not add it.

- [ ] **Step 7: Run real MinIO GREEN twice.**

The integration test reads only `TEST_S3_*`, creates `iap-project-skill-activation-<uuid>`, calls the default bootstrap twice through environment mapping, verifies HEAD, and deletes only that empty bucket in `finally`. Missing configuration skips ordinary mode; service mode must report zero skips.

- [ ] **Step 8: Run regression and commit Task 5.**

Run package storage, bootstrap, deployment, existing Skill MinIO and new real bootstrap tests. Run `docker compose config` and the actual operations service against a plan UUID bucket, twice. Secure-fetch/merge, rerun, stage only Task 5 files and commit:

```text
feat: initialize project skill storage explicitly
```

## Task 6: Production Main Application and Release Rehearsal

**Files:**
- Create: `backend/tests/integration/test_project_skill_main_app.py`
- Modify: `docs/deployment/project-skill-production-activation.md`
- Modify: `backend/README.md`
- Create: `docs/superpowers/plans/2026-09-09-project-skill-production-activation-verification.md`

**Interfaces:**
- Consumes: enabled production `app.main.app`, real `SessionFactory`, current migration head, default Skill storage, Cookie request context and existing Project Skill router/service.
- Produces: cross-boundary acceptance evidence and release/rollback runbook; no new production Python interface.

- [ ] **Step 1: Build a guarded real-main integration fixture.**

The module skips unless all of these are present before importing `app.main`: `IAP_PROJECT_SKILLS_API_ENABLED=true`, `DATABASE_URL`, `TEST_DATABASE_URL`, `TEST_S3_ENDPOINT`, `TEST_S3_ACCESS_KEY`, `TEST_S3_SECRET_KEY`. Service mode must map the `TEST_S3_*` values to `IAP_OBJECT_STORAGE_*` and inject a UUID-owned `IAP_SKILL_BUCKET` before import. It must then require:

```python
assert make_url(os.environ["DATABASE_URL"]).database == (
    "iap_project_skill_activation_test_20260909_a"
)
assert make_url(os.environ["TEST_DATABASE_URL"]).database == (
    "iap_project_skill_activation_test_20260909_a"
)
assert not make_url(os.environ["TEST_DATABASE_URL"]).query
```

After connecting, assert `current_database()` equals the same exact name before creating fixture rows. Use UUID unit/user/project/session IDs, `seed_builtin_catalogue`, active memberships, the built-in `project_admin` project binding, and an `AuthSession` whose token hash is SHA-256 of a random test token. Use a UUID Skill bucket created by Task 5 and remove only this test's objects/bucket.

- [ ] **Step 2: Write actual production app RED/GREEN acceptance.**

Using `TestClient(app.main.app)` and no dependency overrides, assert:

- OpenAPI contains the complete Project Skill method map and old `/api/skills`.
- missing Cookie GET returns 401;
- valid Cookie GET returns 200 and only the fixture project scope;
- valid Cookie POST without `X-CSRF-Token` returns 403 and writes no object/row/audit;
- valid Cookie POST with CSRF returns 201, stores its object under `unit/project/skill/`, and writes one successful audit;
- foreign Origin returns 403;
- exact surrogate bytes with Cookie and CSRF return 422 and leave row/audit/object counts unchanged.

The test must not replace `_service`, `get_session`, `require_request_context`, middleware or storage factory.

- [ ] **Step 3: Run focused real-main integration with zero skips.**

Run in a fresh pytest process through service mode so feature configuration is set before module import. Require zero skips and record counts. Then run existing isolated Project Skill Cookie/API suites to prove production wiring did not alter their contracts.

- [ ] **Step 4: Perform an image startup rehearsal without exposing secrets.**

Use a plan-owned ignored Python helper with the Docker SDK. It reads existing test-container credentials into memory, creates an API container from `intelligent-agent-platform-api:local` with:

- host-reachable dedicated service database URL;
- `IAP_PROJECT_SKILLS_API_ENABLED=true`;
- plan UUID Skill bucket configuration;
- a random unused host port;
- no source mounts and no business database URL.

The helper waits for `/api/health`, fetches `/openapi.json`, verifies both old and new Skill paths, then stops/removes only its named container in `finally`. It prints status/route booleans only, never environment values. A second launch against the migration database at revision 26 must exit before health; logs must contain the stable revision mismatch and exclude a sentinel password. Upgrade that dedicated migration database back to head immediately after the proof.

- [ ] **Step 5: Complete the durable runbook and verification report.**

Document exact build, `migrate`, `skill-storage-init`, revision check, feature enable, health/OpenAPI/auth smoke checks and flag-only rollback. State that front-end/browser, Agent runtime and remote deployment are excluded. Record every command's exit code, passed/failed/skipped counts, warnings, test database/bucket ownership and initial RED evidence; never record credentials.

- [ ] **Step 6: Run focused compatibility regression.**

Sequentially run Project Skill, old Skill, identity/session, audit, Agent, collaboration and runtime boundary suites. Run real PostgreSQL/MinIO integration selections with zero configuration skips. Do not run timing-sensitive suites concurrently.

- [ ] **Step 7: Fetch, merge, rerun affected gates and commit Task 6.**

Stage exactly the four Task 6 files: `backend/tests/integration/test_project_skill_main_app.py`, `docs/deployment/project-skill-production-activation.md`, `backend/README.md`, and `docs/superpowers/plans/2026-09-09-project-skill-production-activation-verification.md`. Commit:

```text
test: verify project skill production activation
```

## Final Branch Gates

- [ ] Generate a whole-branch review package from merge-base to HEAD and run one final review for spec compliance, security, deployment safety and code quality.
- [ ] If review reports findings, use the plan's single consolidated final fix wave and scoped re-review; do not make unreviewed controller fixes.
- [ ] Clear all external `TEST_*`, keep `DATABASE_URL` on the dedicated service database, and run the complete backend suite once with `-p no:cacheprovider`.
- [ ] Run dedicated real PostgreSQL/MinIO integration selections separately with zero skips; do not add overlapping test totals as unique counts.
- [ ] Run `python -m pip check`, Ruff, Black check, `git diff --check`, `docker compose config`, API image build and Uvicorn-command inspection.
- [ ] Prove protected paths remain unchanged from plan base: old Skill router/service, frontend, agents, collaboration and runtime. `backend/app/main.py` is the only approved application composition change.
- [ ] Secure-fetch and merge `origin/main`; if source changes, rerun every affected gate before the final targeted documentation update.
- [ ] Finalize the durable verification report and commit it. Do not claim remote deployment, frontend/browser validation or Agent runtime integration.
- [ ] After evidence is durable in Git, remove only this plan's ignored SDD scratch directory; preserve worktree, virtual environment, sibling plans and user files unless the final integration choice authorizes their cleanup.
- [ ] Invoke `superpowers:finishing-a-development-branch` and present the user with local merge, push/PR, or keep-branch choices. Do not integrate automatically.
