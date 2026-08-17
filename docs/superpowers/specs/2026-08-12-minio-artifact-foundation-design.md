# MinIO Artifact 基础设计

## 目标

为 Agent Run、知识库和沙箱建立统一的 Artifact 文件抽象：文件内容保存到 MinIO，PostgreSQL 保存元数据、版本、权限和 Run 关联，客户端通过短期签名 URL 下载。

## 范围

- 增加 MinIO 本地开发服务和 API 配置。
- 增加 Artifact 元数据、Run 关联和资源范围字段。
- 提供受权限保护的创建、查询和短期下载接口。
- 支持小文件直接上传与服务端生成下载 URL。
- 不在本阶段实现知识库解析、沙箱 Worker、LangGraph 或前端完整成果渲染。

## 边界与安全

- API 不向客户端暴露 MinIO 长期凭据。
- 对象键由服务端生成，禁止客户端指定任意路径。
- 下载前重新校验单位、项目、用户和资源域权限。
- Run、消息、检查点只保存 `artifact_id` 和摘要，不保存二进制内容。
- Artifact 删除默认软删除；已被 Run 或审计引用的对象不可物理删除。

## 数据流

```text
客户端/Worker -> Artifact API -> MinIO 对象 -> PostgreSQL 元数据
客户端 <- 短期签名下载 URL <- Artifact API
```

## 兼容性

Artifact API 复用现有认证与 `RequestContext`，不改变现有 Agent Run、审批和审计接口；后续 Deep Agents、LangGraph、知识库和 Sandbox Worker 只依赖 Artifact Service 接口。

## 验收标准

- 未授权用户不能查询或下载其他单位/项目 Artifact。
- 上传对象键不受客户端路径控制。
- 下载 URL 具有过期时间，过期后不能继续访问。
- Run 可关联 Artifact，Artifact 列表按当前权限返回。
- PostgreSQL 不保存文件正文，MinIO 不保存业务权限判断。
