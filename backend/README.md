# Intelligent Agent Platform API

## 本地启动

```powershell
docker compose up -d postgres
$env:DATABASE_URL = "postgresql+psycopg://iap:iap@127.0.0.1:5432/iap"
$env:IAP_ALLOW_DEV_IDENTITY = "true"
cd backend
python -m pip install -r requirements.txt
python -m alembic upgrade head
python -m uvicorn app.main:app --reload --port 8000
```

`DATABASE_URL` 用于会话、消息、Agent Run 和 Run Event。修改模型后先创建 Alembic 迁移，再执行 `python -m alembic upgrade head`。

`IAP_ALLOW_DEV_IDENTITY` 默认是 `false`。开启后，请求仍必须同时提供 `X-Unit-ID`、`X-User-ID` 和 `X-Project-ID`；这只是本地开发适配器，不得作为生产认证。生产环境应使用可信认证会话，并保持该开关关闭。

根目录 Compose 同样默认关闭开发身份。需要在容器化本机环境调试会话或审计页面时，必须同时显式设置 `IAP_ALLOW_DEV_IDENTITY=true`、`VITE_DEV_UNIT_ID`、`VITE_DEV_USER_ID`、`VITE_DEV_PROJECT_ID` 和 `VITE_DEV_USER_ROLES` 并重新构建 Web 镜像；`VITE_DEV_USER_ROLES` 是逗号分隔的 `user`、`project_admin`、`unit_auditor` 集合。这些变量只用于非敏感测试身份，不得用于生产部署。

## 会话和运行接口

- `POST /api/conversations`
- `GET /api/conversations`
- `GET /api/conversations/{conversation_id}`
- `GET /api/conversations/{conversation_id}/messages`
- `POST /api/conversations/{conversation_id}/messages`
- `GET /api/agent-runs`
- `GET /api/agent-runs/{run_id}`
- `GET /api/agent-runs/{run_id}/events`
- `GET /api/agent-runs/{run_id}/tool-invocations`

事件接口返回有限 SSE 回放并关闭连接。通过 `Last-Event-ID` 请求头传入已处理的事件序号即可恢复读取；持续事件流将在沙箱执行服务接入后实现。

`GET /api/agent-runs` 仅查询请求身份所属的当前项目和当前用户数据，支持 `page`、`page_size` 分页（`page_size` 最大为 100），以及 `status`、`actor_id`、`query`、`started_after`、`started_before` 筛选。响应包含当前筛选范围的分页记录、总数和状态/工具调用汇总；`query` 匹配会话标题或 Run ID，`started_after` 和 `started_before` 必须使用包含 `Z` 或明确时区偏移的 timezone-aware ISO 8601 时间，且须满足 `started_after <= started_before`。

运行详情继续复用既有的 `GET /api/agent-runs/{run_id}`、`GET /api/agent-runs/{run_id}/events` 和 `GET /api/agent-runs/{run_id}/tool-invocations`。这些接口沿用相同的项目与用户范围隔离；工具调用接口返回 Tool Gateway 按敏感字段名脱敏后持久化的 `arguments_summary`、`result_summary` 及错误码、耗时等审计字段。调用方仍须避免把秘密放在非敏感字段中，审计接口不应作为秘密存储。

当前生产 Dispatcher 会让新 Run 保持 `queued`，不会伪造智能体回复或沙箱运行状态。

## 统一审计接口

- `GET /api/audit/events`
- `GET /api/audit/events/{event_id}`
- `GET /api/audit/events/{event_id}/related`

列表支持 `page`、`page_size`（最大 100）、`category`、`source`、`action`、`status`、`risk_level`、`project_id`、`user_id`、`query`、`occurred_after` 和 `occurred_before`。`query` 匹配事件、Trace、Run 或资源标识；时间边界必须是带 `Z` 或明确偏移的 timezone-aware ISO 8601 值，且开始时间不得晚于结束时间。

读取范围由服务端角色约束：`unit_auditor` 可读当前单位，`project_admin` 可读当前项目，普通 `user` 仅可读当前项目中的本人事件；客户端筛选不能扩大该范围。详情和关联事件对“不存在”与“越权”统一返回安全 404，避免暴露资源是否存在。

审计记录是追加写入的独立事件，不提供更新或删除 API。写入前仅保留显式允许的元数据字段，并按敏感字段名递归脱敏、限制层级与大小；不得将凭据、口令、客户数据或原始 Prompt 写入摘要或元数据。当前已接入 Agent、tool、LLM 运行事件及 Agent、MCP、Tool、模型供应商等选定管理操作。知识库真实审计、真实 MCP 执行、沙箱审计、导出、保留期自动化和事件总线尚未实现。

升级统一审计表后，运维人员可在 API 启动前或独立维护窗口显式执行受控回填：

```powershell
cd backend
python -m alembic upgrade head
python -m app.audit.backfill
```

该命令按批提交，并以每个 Agent Run 的稳定幂等键写入快照，可安全重跑；API 启动不会自动回填。仅对目标测试或维护数据库执行，并在执行前确认 `DATABASE_URL`。

## 大模型供应商配置

模型供应商、智能体和 MCP 配置与会话运行数据统一保存在 `DATABASE_URL` 指定的 PostgreSQL。内置供应商定义位于 `app/model_providers/registry.py`。

旧版 SQLite 数据可通过以下命令一次性导入；默认读取 `/data/model-providers.db`、`/data/agents.db` 和 `/data/mcp.db`：

```powershell
python -m alembic upgrade head
python -m app.migrations.sqlite_to_postgres
```

可通过 `LEGACY_SQLITE_DATA_DIR` 或三个 `LEGACY_*_DATABASE` 环境变量调整源文件路径。导入按数据域幂等执行，只导入空的 PostgreSQL 目标表，不删除 SQLite 文件。

## 测试

```powershell
cd backend
python -m pytest -q
```
