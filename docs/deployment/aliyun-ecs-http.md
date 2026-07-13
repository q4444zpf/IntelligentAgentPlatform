# 阿里云 ECS HTTP 部署运维指南

## 当前部署

- 公网地址：`http://39.108.91.166`
- 健康检查：`http://39.108.91.166/api/health`
- 发布根目录：`/opt/intelligent-agent-platform`
- 当前版本链接：`/opt/intelligent-agent-platform/current`
- Compose 项目名：`intelligent-agent-platform`
- 数据卷：`intelligent-agent-platform_model-provider-data`

服务器使用 Docker Compose 运行 `web` 和 `api` 两个服务。`web` 通过 TCP 80 提供前端并反向代理 `/api/`；`api` 仅在 Compose 内部网络监听 8000。

## 状态与日志

```bash
cd /opt/intelligent-agent-platform/current
docker compose ps
docker compose logs --tail=200
docker compose logs -f api
docker compose logs -f web
```

## 启停与重启

```bash
cd /opt/intelligent-agent-platform/current
docker compose up -d --no-build
docker compose restart
docker compose stop
```

不要使用 `docker compose down -v`，该命令会删除包含 SQLite 数据库的持久化卷。

## 健康检查

```bash
curl -fsS http://127.0.0.1/api/health
curl -fsS http://39.108.91.166/api/health
```

预期响应：

```json
{"status":"ok"}
```

## 数据备份

先查询数据卷挂载位置：

```bash
docker volume inspect intelligent-agent-platform_model-provider-data \
  --format '{{.Mountpoint}}'
```

在应用写入较少的维护窗口内备份：

```bash
mkdir -p /opt/intelligent-agent-platform/backups
cd /opt/intelligent-agent-platform/current
docker compose stop api
tar -C /var/lib/docker/volumes/intelligent-agent-platform_model-provider-data/_data \
  -czf /opt/intelligent-agent-platform/backups/model-provider-data-$(date +%Y%m%d-%H%M%S).tar.gz .
docker compose start api
```

备份完成后执行健康检查。生产环境应将备份复制到 ECS 之外的受控存储。

## 更新

每次更新使用新的时间戳目录：

```bash
release=/opt/intelligent-agent-platform/releases/YYYYMMDD-HHMMSS
mkdir -p "$release"
```

将经过审计的源文件或已构建镜像上传到新目录。若服务器可访问 Docker Hub，可在发布目录运行：

```bash
docker compose build
docker compose up -d
```

若服务器无法访问 Docker Hub，在可信构建机执行 `docker compose build`，将两个镜像标记为 `intelligent-agent-platform-api:latest` 和 `intelligent-agent-platform-web:latest`，使用 `docker save` 导出并在服务器使用 `docker load` 导入，然后执行：

```bash
docker compose up -d --no-build
```

健康检查通过后再原子更新当前版本链接：

```bash
ln -sfn "$release" /opt/intelligent-agent-platform/current.new
mv -Tf /opt/intelligent-agent-platform/current.new /opt/intelligent-agent-platform/current
```

## 回滚

确认上一发布目录存在，然后从该目录启动相同的固定 Compose 项目：

```bash
previous=/opt/intelligent-agent-platform/releases/PREVIOUS_TIMESTAMP
cd "$previous"
docker compose up -d --no-build
curl -fsS http://127.0.0.1/api/health
ln -sfn "$previous" /opt/intelligent-agent-platform/current.new
mv -Tf /opt/intelligent-agent-platform/current.new /opt/intelligent-agent-platform/current
```

固定项目名保证更新和回滚继续使用同一个 SQLite 数据卷。

## 安全注意事项

- 不要在仓库、Compose 文件、镜像或命令历史中保存 SSH 密码与模型密钥。
- 公网只需开放 SSH 管理端口和 TCP 80；不要映射后端 8000。
- 首次部署使用全新的服务器数据库，不包含本地模型供应商密钥。
- 应尽快更换部署时使用过的密码，并改为 SSH 密钥登录。
- 配置域名后，应启用 HTTPS，并将 HTTP 重定向到 HTTPS。
