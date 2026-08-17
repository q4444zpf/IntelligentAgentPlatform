# MinIO Artifact 基础 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 建立受权限控制的 MinIO Artifact 存储基础，供后续 Skill、知识库、LangGraph 和沙箱复用。

**Architecture:** FastAPI Artifact Service 负责认证、权限、元数据和签名 URL；MinIO 只保存对象内容；PostgreSQL 保存 Artifact 元数据和 Run 关联。对象键由服务端生成并按租户、项目和 Artifact ID 隔离。

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, PostgreSQL, MinIO S3 API, boto3-compatible client, pytest.

## Global Constraints

- 生产数据库使用 PostgreSQL；SQLite 仅用于单元测试适配器。
- 所有查询必须使用当前 `RequestContext` 的单位和项目范围。
- 密钥只来自环境变量，不进入 API 响应、日志、审计、Prompt 或检查点。
- 使用 TDD：每个新行为先写失败测试并确认失败，再实现最小代码。

### Task 1: Object storage configuration and MinIO service

**Files:**
- Modify: `compose.yaml`
- Modify: `.env.example`
- Modify: `backend/requirements.txt`
- Create: `backend/app/artifacts/storage.py`
- Test: `backend/tests/artifacts/test_storage.py`

**Interfaces:**
- Produces `ObjectStorage.put_bytes(object_key, data, content_type) -> None`.
- Produces `ObjectStorage.presigned_get_url(object_key, expires_seconds) -> str`.

- [ ] Write a test proving the storage adapter sends bytes and returns a bounded download URL through an injected client.
- [ ] Run `pytest tests/artifacts/test_storage.py -q` and verify the new adapter test fails because the adapter is absent.
- [ ] Implement the injected S3-compatible adapter with environment-based endpoint, bucket, access key and secret key.
- [ ] Add MinIO healthcheck and a non-secret local bucket configuration to Compose and `.env.example`.
- [ ] Run the focused storage test and verify it passes.

### Task 2: Artifact metadata model and migration

**Files:**
- Create: `backend/app/artifacts/models.py`
- Create: `backend/app/artifacts/schemas.py`
- Create: `backend/alembic/versions/20260812_16_artifacts.py`
- Test: `backend/tests/artifacts/test_models.py`

**Interfaces:**
- `ArtifactRecord` stores `id`, `unit_id`, `project_id`, `owner_id`, `scope`, `run_id`, `object_key`, `filename`, `content_type`, `size_bytes`, `sha256`, `status`, timestamps and soft-delete time.
- `ArtifactInfo` never includes storage credentials.

- [ ] Write tests for required scope fields, positive size, safe status values and stable SHA-256 metadata.
- [ ] Run the model tests and verify failure before implementation.
- [ ] Implement the SQLAlchemy model, Pydantic schemas and Alembic migration with indexes on scope, run and owner.
- [ ] Run model tests and migration upgrade/downgrade checks.

### Task 3: Artifact service and protected API

**Files:**
- Create: `backend/app/artifacts/service.py`
- Create: `backend/app/artifacts/router.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/artifacts/test_api.py`

**Interfaces:**
- `POST /api/artifacts` creates metadata and uploads bytes.
- `GET /api/artifacts` lists only artifacts visible to the current request context.
- `GET /api/artifacts/{artifact_id}` returns metadata.
- `GET /api/artifacts/{artifact_id}/download` returns a short-lived signed URL.
- `POST /api/runs/{run_id}/artifacts` associates an existing artifact with a visible Run.

- [ ] Write tests for authorized listing, cross-project denial, server-owned object keys, upload metadata, and signed download response.
- [ ] Run the API tests and verify expected authorization failures before implementation.
- [ ] Implement service methods using current request-context dependencies and the object-storage adapter.
- [ ] Register routes and ensure binary data is not copied into Run events or audit metadata.
- [ ] Run artifact tests plus identity and conversation regressions.

### Task 4: Verification and deployment handoff

**Files:**
- Modify: `backend/README.md`
- Modify: `docs/deployment/aliyun-ecs-http.md`
- Test: `backend/tests/artifacts/test_api.py`

- [ ] Run the full artifact test module and targeted identity/conversation tests.
- [ ] Build Compose images and verify PostgreSQL, MinIO, API and Web healthchecks.
- [ ] Verify a signed URL expires and that an unauthorized project cannot download the object.
- [ ] Document required environment variables and backup expectations for MinIO data.
