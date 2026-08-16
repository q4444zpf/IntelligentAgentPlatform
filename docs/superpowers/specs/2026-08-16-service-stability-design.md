# 服务稳定性与状态诊断设计

## 目标

降低 MinIO 因容器退出造成的成果文件访问中断，并让工作台以不泄露内部信息的方式展示基础服务状态。

## 范围

- 为 `minio` 配置 `restart: unless-stopped`，沿用现有健康检查与数据卷。
- 服务诊断接口继续返回 `healthy`、`unhealthy`、`disabled` 三类状态；失败详情使用稳定的用户可读文案，不返回内部 URL、凭据、文件路径或异常堆栈。
- 工作台继续使用现有紧凑服务卡片、手动刷新和 5 分钟轮询，仅校验状态文案映射。
- 增加 Compose 配置回归测试及后端服务诊断分支测试。

## 非目标

- 不实现告警通知、自动拉起服务编排或健康状态持久化。
- 不修改 Agent、Skill、MCP、LangGraph、DeepAgents 执行链。

## 设计

`compose.yaml` 中 MinIO 增加 `restart: unless-stopped`。API 的 MinIO 检查继续通过 S3 `list_buckets` 验证可用性；任何连接、认证或 SDK 异常统一映射为 `unhealthy/unreachable`。Workflow Runner 和 Sandbox Launcher 沿用现有安全状态映射。前端将 `healthy` 显示为“正常”、`disabled` 显示为“未启用”、其他状态显示为“异常”。

## 验证

- 后端：服务状态接口测试覆盖五项服务顺序、MinIO 健康和异常分支、Compose 中 MinIO 重启策略。
- 前端：工作台源码测试继续验证手动刷新和 300000 毫秒轮询。
- 完成后运行相关测试集，并检查工作树只包含本阶段文件。
