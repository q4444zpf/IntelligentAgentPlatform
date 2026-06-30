# 通用智能体平台前端原型

这是一个纯前端演示原型，使用 Vue 3、TypeScript、Vite、Ant Design Vue 实现，并按 Vue Vben Admin 的典型思路组织路由、菜单、权限、布局、Mock 数据和业务模块。

当前仓库原本没有现成 Vue Vben Admin 工程，因此本目录先提供可运行、可演示的前端原型骨架。后续如接入完整 Vben Admin，可将 `src/views`、`src/mock` 中的业务页面、Mock 数据和页面配置迁移到 Vben 的 `apps/web-antd` 或对应业务应用目录。

## 运行命令

```bash
npm install
npm run dev
npm run build
```

默认开发地址：

```text
http://localhost:5173
```

## 已实现页面

- 工作台
- AI 对话
- 个人资源空间
- 公用资源库
- 智能体管理
- Prompt 管理
- MCP 管理
- Skill / Tool 管理
- 知识库管理
- 流程编排
- 多智能体协同
- 大模型配置
- 系统集成
- 用户与权限
- 审计与日志
- 沙箱监控
- 系统设置

## 原型能力

- Vue Router 路由
- 菜单配置
- 权限码 Mock
- Ant Design Vue 企业级 UI
- Mock 数据服务
- 个人资源、公用资源、MCP、Skill、大模型供应商、沙箱任务 Mock
- AI 对话静态原型
- 通用管理列表页
- 沙箱执行状态展示
