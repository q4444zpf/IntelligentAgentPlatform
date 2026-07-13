# 阿里云 ECS 公网 HTTP 部署实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前 Vue/FastAPI 应用以 Docker Compose 方式部署到阿里云 ECS，并通过公网 IP 的 TCP 80 提供 Web 和 API。

**Architecture:** Nginx 容器托管前端静态资源并反向代理 `/api/`，FastAPI 容器仅在内部网络监听 8000。SQLite 数据文件挂载到持久化卷，应用容器可安全重建。

**Tech Stack:** Docker Compose、Nginx、Node.js 20、Vue 3/Vite、Python 3.12、FastAPI/Uvicorn、SQLite

## Global Constraints

- 仅使用公网 IP 和 HTTP，不配置域名或 TLS。
- 不上传本地 `backend/data/model-providers.db` 或任何密钥。
- 保留所有现有未提交用户修改，不覆盖或清理工作区。
- 公网只暴露 TCP 80；API 8000 不映射到宿主机。
- 部署前检查 80 端口冲突；存在冲突时停止并请求用户决定。

---

### Task 1: 添加容器化部署配置

**Files:**
- Create: `.dockerignore`
- Create: `backend/Dockerfile`
- Create: `frontend/Dockerfile`
- Create: `frontend/nginx.conf`
- Create: `compose.yaml`

**Interfaces:**
- Consumes: `frontend/package-lock.json`、`backend/requirements.txt`、FastAPI `/api/health`
- Produces: Compose 服务 `web` 和 `api`，持久化卷 `model-provider-data`

- [ ] **Step 1: 写入部署忽略规则**

```dockerignore
.git
.github
.claude
.env
.env.*
**/__pycache__
**/*.pyc
**/.pytest_cache
**/node_modules
**/dist
**/*.log
backend/data/*.db
backend/data/*.json
backend/data/*.migrated
docs
tests
```

- [ ] **Step 2: 写入后端 Dockerfile**

```dockerfile
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
RUN mkdir -p /data && chown -R nobody:nogroup /data /app
USER nobody
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
```

- [ ] **Step 3: 写入前端多阶段 Dockerfile 和 Nginx 配置**

```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
```

```nginx
server {
  listen 80;
  server_name _;
  root /usr/share/nginx/html;
  index index.html;

  location /api/ {
    proxy_pass http://api:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_connect_timeout 10s;
    proxy_read_timeout 120s;
  }

  location / {
    try_files $uri $uri/ /index.html;
  }
}
```

- [ ] **Step 4: 写入 Compose 配置**

```yaml
services:
  api:
    build:
      context: .
      dockerfile: backend/Dockerfile
    environment:
      MODEL_PROVIDER_DATABASE: /data/model-providers.db
    volumes:
      - model-provider-data:/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"]
      interval: 10s
      timeout: 3s
      retries: 6
      start_period: 10s

  web:
    build:
      context: .
      dockerfile: frontend/Dockerfile
    depends_on:
      api:
        condition: service_healthy
    ports:
      - "80:80"
    restart: unless-stopped

volumes:
  model-provider-data:
```

- [ ] **Step 5: 校验 Compose 配置**

Run: `docker compose config`
Expected: 配置解析成功且 `api` 没有 `ports` 字段。

- [ ] **Step 6: 提交部署配置**

```bash
git add .dockerignore backend/Dockerfile frontend/Dockerfile frontend/nginx.conf compose.yaml
git commit -m "feat: add container deployment"
```

### Task 2: 本地构建与测试

**Files:**
- Test: `backend/tests/test_model_providers.py`
- Verify: `frontend/src/**`

**Interfaces:**
- Consumes: Task 1 的 Docker 和 Compose 配置
- Produces: 可发布且通过测试的工作区

- [ ] **Step 1: 运行后端测试**

Run: `python -m pytest backend/tests -q`
Expected: 所有测试通过。

- [ ] **Step 2: 运行前端生产构建**

Run: `npm run build --prefix frontend`
Expected: `vue-tsc` 和 Vite 构建成功。

- [ ] **Step 3: 构建容器镜像**

Run: `docker compose build`
Expected: `web` 与 `api` 镜像构建成功；构建上下文不包含本地数据库。

### Task 3: 服务器预检

**Files:**
- Inspect only: ECS operating system, Docker state, ports, storage, firewall

**Interfaces:**
- Consumes: 已授权的 ECS SSH 登录信息
- Produces: 无端口冲突、满足部署要求的目标环境

- [ ] **Step 1: 建立 SSH 连接并识别环境**

Run remotely: `cat /etc/os-release; uname -m; df -h /; command -v docker || true; docker compose version 2>/dev/null || true`
Expected: 支持 Docker 的 x86_64 或 aarch64 Linux，根分区空间足够。

- [ ] **Step 2: 检查端口和已有容器**

Run remotely: `ss -lntp; docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null || true`
Expected: TCP 80 未被不相关服务占用；如被占用则停止部署并请求决定。

- [ ] **Step 3: 安装缺失的 Docker Engine 与 Compose 插件**

Run: 使用检测到的 Linux 发行版官方仓库安装，并启用 `docker` 服务。
Expected: `docker version` 和 `docker compose version` 成功，Docker 服务为 active。

### Task 4: 上传并启动发布

**Files:**
- Deploy: `/opt/intelligent-agent-platform/releases/<timestamp>/`
- Persist: Docker volume `model-provider-data`

**Interfaces:**
- Consumes: 通过 Task 2 验证的源文件和 Task 3 的服务器环境
- Produces: 监听公网 TCP 80 的运行中服务

- [ ] **Step 1: 创建排除敏感文件的发布归档**

Run: 使用 Git 跟踪文件与显式新增应用文件构建归档，并检查归档列表不含 `.env`、`.git`、`node_modules`、`dist` 或 `backend/data/*`。
Expected: 归档只包含运行和构建所需文件。

- [ ] **Step 2: 上传并解压到时间戳发布目录**

Run remotely: `mkdir -p /opt/intelligent-agent-platform/releases/<timestamp>`，上传后解压。
Expected: `compose.yaml`、前后端 Dockerfile 和源文件存在。

- [ ] **Step 3: 构建并启动服务**

Run remotely: `docker compose up -d --build`
Expected: `api` 健康，`web` 处于 running 状态。

- [ ] **Step 4: 记录当前发布**

Run remotely: 更新 `/opt/intelligent-agent-platform/current` 符号链接指向成功发布目录。
Expected: 符号链接解析到本次发布。

### Task 5: 上线验证与交付

**Files:**
- Inspect only: 服务状态和 HTTP 响应

**Interfaces:**
- Consumes: Task 4 的运行服务
- Produces: 可从公网访问且具有持久化能力的部署结果

- [ ] **Step 1: 检查容器状态和日志**

Run remotely: `docker compose ps; docker compose logs --tail=100`
Expected: 无启动错误，API healthcheck 为 healthy。

- [ ] **Step 2: 验证服务器本机 HTTP**

Run remotely: `curl -fsS http://127.0.0.1/api/health`
Expected: `{"status":"ok"}`。

- [ ] **Step 3: 验证公网 API 与前端**

Run locally: `curl http://39.108.91.166/api/health` 和请求 `/`。
Expected: API HTTP 200；首页 HTTP 200 且内容为构建后的前端 HTML。

- [ ] **Step 4: 验证重启恢复**

Run remotely: `docker compose restart`，等待健康后再次执行健康检查。
Expected: 服务恢复并保持 HTTP 200。

- [ ] **Step 5: 更新部署文档并提交**

在根 `README.md` 或 `docs/` 中记录启动、更新、日志、备份和回滚命令，不写入服务器密码。

Run: `git add <deployment-doc>; git commit -m "docs: document production deployment"`
Expected: 文档提交仅包含安全、可复用的运维说明。
