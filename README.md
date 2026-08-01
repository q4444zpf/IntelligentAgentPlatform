# 水利智能体平台

面向水利业务的专业智能体平台，规划覆盖水模型管理、模型上传与注册、控制模型、拖拽式流程编排、智能体与技能管理、多智能体协作、多用户权限、专属会话、大模型配置，以及 Web、嵌入式 AI Chatbox 和桌面客户端集成。

## 当前实现

- Web 管理控制台原型，包含工作台、AI 对话、资源库、智能体、Prompt、MCP、Skill / Tool、知识库、流程编排、多智能体协同、大模型配置、系统集成、用户权限、审计日志、沙箱监控和系统设置等页面。
- FastAPI 后端服务，提供健康检查、会话与运行事件、平台运行总览和大模型供应商配置 API。
- 项目及用户范围内的会话、消息、Agent Run 和 Run Event 使用 PostgreSQL 持久化，并支持有限、可恢复的 SSE 事件回放。
- 会话、模型供应商、智能体和 MCP 配置统一使用 PostgreSQL 持久化。
- 支持前后端分别启动，也可通过根目录脚本联合启动。

## 技术栈

### 前端

- Vue 3
- TypeScript
- Vite
- Ant Design Vue
- Vue Router
- Pinia

### 后端

- Python 3
- FastAPI
- Uvicorn
- Pydantic
- HTTPX
- SQLAlchemy 2 / Alembic
- PostgreSQL 16（业务配置、会话和运行）
- SQLite（仅作为旧数据一次性迁移源和单元测试适配器）
- Pytest

## 目录结构

```text
.
├── frontend/       Web 控制台和嵌入式交互界面
├── backend/        API、模型供应商配置和数据持久化
├── start-dev.ps1   Windows 前后端联合启动脚本
└── docs/           架构、接口和部署文档（规划）
```

## 环境要求

- Node.js 20 或更高版本
- npm
- Python 3.11 或更高版本
- Docker Desktop 或兼容的 Docker Compose（用于 PostgreSQL 和整套容器启动）

## 快速启动

首次运行时分别安装前后端依赖：

```powershell
cd frontend
npm install

cd ..\backend
python -m pip install -r requirements.txt
```

回到项目根目录，联合启动前后端：

```powershell
.\start-dev.ps1
```

启动后可访问：

- Web 控制台：<http://127.0.0.1:5173>
- 后端 API：<http://127.0.0.1:8000>
- Swagger API 文档：<http://127.0.0.1:8000/docs>

## PostgreSQL 会话基础服务

先在项目根目录启动 PostgreSQL：

```powershell
docker compose up -d postgres
$env:DATABASE_URL = "postgresql+psycopg://iap:iap@127.0.0.1:5432/iap"
$env:IAP_ALLOW_DEV_IDENTITY = "true"
cd backend
python -m alembic upgrade head
python -m uvicorn app.main:app --reload --port 8000
```

另开终端启动前端，并为本地请求提供显式的开发身份：

```powershell
cd frontend
$env:VITE_DEV_USER_ID = "dev-user"
$env:VITE_DEV_PROJECT_ID = "dev-project"
npm run dev
```

`IAP_ALLOW_DEV_IDENTITY` 默认关闭。`X-User-ID`、`X-Project-ID` 及对应的 Vite 变量仅用于本地开发，不是生产认证方案；生产环境必须接入服务端认证会话并保持该开关关闭。

## 分别启动

启动后端：

```powershell
cd backend
python -m uvicorn app.main:app --reload --port 8000
```

启动前端：

```powershell
cd frontend
npm run dev
```

前端开发服务器会将 `/api` 请求代理到 `http://127.0.0.1:8000`。

## 后端说明

后端入口为 `backend/app/main.py`，当前提供以下接口组：

- `GET /api/health`：服务健康检查。
- `GET /api/platform/overview`：平台运行总览，包括供应商、模型和当前默认模型状态。
- `/api/models`：查询和管理大模型供应商。
- `/api/models/{provider_id}/config`：配置供应商连接信息。
- `/api/models/{provider_id}/models`：添加和管理供应商模型。
- `/api/models/{provider_id}/discover`：发现供应商可用模型。
- `/api/models/{provider_id}/test`：测试供应商连接。
- `/api/models/active`：查询或设置当前默认模型。
- `POST/GET /api/conversations`：创建或查询当前项目、当前用户的会话。
- `GET /api/conversations/{conversation_id}/messages`：读取持久化消息。
- `POST /api/conversations/{conversation_id}/messages`：写入用户消息并创建等待执行的 Agent Run。
- `GET /api/agent-runs/{run_id}`：读取 Run 状态。
- `GET /api/agent-runs/{run_id}/events`：通过 `Last-Event-ID` 恢复读取有限 SSE 事件。

模型供应商、智能体和 MCP 运行数据统一写入 `DATABASE_URL` 指定的 PostgreSQL。更新已有部署时，API 容器会在 Alembic 升级后检查 `/data/model-providers.db`、`/data/agents.db` 和 `/data/mcp.db`；仅当对应 PostgreSQL 表为空时执行一次性导入：

旧版容器首次升级前，先把位于容器层的 Agent/MCP SQLite 数据库备份到持久卷，避免重建容器时丢失：

```powershell
docker compose exec -T api python -c "import sqlite3; [(lambda s,d: (s.backup(d), d.close(), s.close()))(sqlite3.connect('/app/data/'+n), sqlite3.connect('/data/'+n)) for n in ('agents.db','mcp.db')]"
```

```powershell
$env:DATABASE_URL = "postgresql+psycopg://iap:iap@127.0.0.1:5432/iap"
python -m alembic upgrade head
python -m app.migrations.sqlite_to_postgres
python -m uvicorn app.main:app --reload --port 8000
```

导入命令不会删除或修改 SQLite 源文件，也不会用旧数据覆盖非空 PostgreSQL 表。内置供应商定义仍位于 `backend/app/model_providers/registry.py`。

## 构建与测试

前端类型检查和生产构建：

```powershell
cd frontend
npm run build
```

后端测试：

```powershell
cd backend
python -m pytest
```

整套容器启动：

```powershell
docker compose up -d --build postgres api web
Invoke-WebRequest -UseBasicParsing http://127.0.0.1/api/health
```

上述命令使用安全默认值，开发身份适配器保持关闭。仅在本机联调会话页面时，显式设置开发身份并重新构建 Web 镜像：

```powershell
$env:IAP_ALLOW_DEV_IDENTITY = "true"
$env:VITE_DEV_USER_ID = "dev-user"
$env:VITE_DEV_PROJECT_ID = "dev-project"
docker compose up -d --build postgres api web
```

不要在生产环境设置这些开发变量。前端身份值会在构建时写入静态资源，不能替代登录认证或服务端授权。

## 配置与安全

- 不要提交 API Key、模型凭据、数据库密码、LLM 密钥或客户水利项目数据。
- 敏感信息应通过环境变量或密钥管理服务提供。
- 示例配置仅使用安全占位值，例如 `LLM_API_KEY=change-me`。
- 执行水库、闸门、泵站等控制命令时，应保留权限校验、审计记录和人工确认机制。

更详细的模块说明参见 [frontend/README.md](frontend/README.md) 和 [backend/README.md](backend/README.md)。
