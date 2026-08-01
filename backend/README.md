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

`IAP_ALLOW_DEV_IDENTITY` 默认是 `false`。开启后，请求仍必须同时提供 `X-User-ID` 和 `X-Project-ID`；这只是本地开发适配器，不得作为生产认证。生产环境应使用可信认证会话，并保持该开关关闭。

根目录 Compose 同样默认关闭开发身份。需要在容器化本机环境调试会话页面时，必须同时显式设置 `IAP_ALLOW_DEV_IDENTITY=true`、`VITE_DEV_USER_ID` 和 `VITE_DEV_PROJECT_ID` 并重新构建 Web 镜像；这些变量不得用于生产部署。

## 会话和运行接口

- `POST /api/conversations`
- `GET /api/conversations`
- `GET /api/conversations/{conversation_id}`
- `GET /api/conversations/{conversation_id}/messages`
- `POST /api/conversations/{conversation_id}/messages`
- `GET /api/agent-runs/{run_id}`
- `GET /api/agent-runs/{run_id}/events`

事件接口返回有限 SSE 回放并关闭连接。通过 `Last-Event-ID` 请求头传入已处理的事件序号即可恢复读取；持续事件流将在沙箱执行服务接入后实现。

当前生产 Dispatcher 会让新 Run 保持 `queued`，不会伪造智能体回复或沙箱运行状态。

## 大模型供应商配置

运行时配置默认保存在 SQLite 数据库 `data/model-providers.db`。可通过环境变量
`MODEL_PROVIDER_DATABASE` 指定其他数据库文件。内置供应商定义位于
`app/model_providers/registry.py`，用户密钥、额外模型、模型参数和默认模型写入数据库。

如果检测到旧版 `data/model-providers.json` 且数据库为空，启动时会自动导入，成功后
将旧文件改名为 `model-providers.json.migrated`。

## 测试

```powershell
cd backend
python -m pytest -q
```
