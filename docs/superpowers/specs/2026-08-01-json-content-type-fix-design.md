# JSON 请求 Content-Type 修复设计

## 问题

前端统一请求函数会将业务对象序列化为 JSON 字符串，但只发送 `Accept: application/json`，没有发送 `Content-Type: application/json`。FastAPI 因此不能把大模型供应商的测试连接和保存请求解析为 `ProviderConfigRequest`，两个接口均返回 HTTP 422。

## 设计

修改 `frontend/src/api/client.ts` 中的统一 `request()`：当请求包含字符串 `body`，且调用方没有显式设置 `Content-Type` 时，自动添加 `Content-Type: application/json`。调用方显式设置的请求头继续拥有最高优先级。

不修改后端请求模型、模型管理页面、数据库结构或界面样式。无请求体的 GET/DELETE 请求不添加 Content-Type；未来需要上传文件或发送其他媒体类型时，可由调用方显式设置请求头，不会被统一客户端覆盖。

## 测试

增加 API 客户端回归测试，验证：

- JSON 字符串请求体自动携带 `Content-Type: application/json`。
- 显式提供的 Content-Type 不会被覆盖。
- 无请求体请求不被强制添加 Content-Type。

完成后运行前端全量测试和生产构建，再通过大模型管理页面验证 DeepSeek 的保存请求不再返回 422，并验证连接测试能够到达后端连接逻辑。
