# 仓库指南

## 项目范围

本仓库用于建设专业水利智能体平台。平台应覆盖水利模型管理、模型上传注册、控制模型、拖拽式编排、智能体管理、Skill 管理、多智能体协同、多用户访问、专有对话管理、大模型设置、AI 对话框集成、Web 系统对接以及桌面端 / 客户端对接。

## 项目结构与模块组织

随着模块落地，建议保持清晰稳定的顶层目录：

- `frontend/`：Web 管理端、流程编排器、嵌入式 AI 对话框和后台管理界面。
- `backend/`：API、认证、用户权限、模型注册和审计日志。
- `ai-services/`：大模型路由、RAG、提示词管理、Skill 执行和多智能体协同。
- `model-services/`：水文、水动力、水资源、洪水预报、调度优化、控制、GIS/遥感和自定义模型服务。
- `desktop-client/`：桌面端智能体交互、本地文件上下文、通知和客户端 SDK 调用。
- `docs/`：架构、接口契约、数据模型、部署和集成文档。
- `tests/`：单元测试、集成测试、流程测试和端到端测试。
- `assets/`：架构图、界面图片和静态资源。

## 构建、测试与开发命令

当前尚未提交构建系统。新增构建体系时，请在各模块 `README.md` 中记录命令，并尽量提供根目录统一命令：

- `npm run dev`：启动 Web 应用。
- `npm test`：运行单元测试。
- `npm run lint`：运行格式化和静态检查。
- `docker compose up`：启动本地基础设施，例如数据库、对象存储、消息队列和向量库。

除非确有需要，不要提交生成的构建产物。

## 编码风格与命名规范

代码标识符使用清晰的领域化英文命名，例如 `ModelRegistryService`、`ControlModelDefinition`、`SkillExecutionRecord`、`ReservoirDispatchAgent`。中文主要用于用户界面文案、提示词和文档。JavaScript/TypeScript/JSON/YAML 使用 2 空格缩进，Python 使用 4 空格缩进。API 路径保持小写并面向资源，例如 `/api/models/{id}/versions`。

## 领域与架构约定

模型管理必须支持文件上传、Docker 镜像、脚本、外部 API、版本管理、参数结构、运行环境配置、权限控制和执行日志。控制模型是一等模型类型，覆盖水库、闸门、泵站、河网、灌区和城市排水等控制对象。

智能体管理必须区分 `web`、`desktop` 和 `common` 运行形态，因为不同端的交互上下文和提示词不同。Web 端智能体侧重页面上下文、业务对象和嵌入式对话；桌面端智能体侧重本地文件、客户端状态、通知以及控制指令的人机确认。

## 测试指南

每次行为变更都应补充测试。重点覆盖模型注册、控制模型校验、Skill 调用、Web/桌面端提示词选择、权限检查、流程执行、对话持久化和第三方集成 API。测试命名应描述预期行为，例如 `registersUploadedHydrologyModel` 或 `usesDesktopPromptWhenClientTypeIsDesktop`。

## 提交与拉取请求规范

当前工作区没有可用 Git 历史，因此后续建议使用简洁的 Conventional Commits，例如 `feat: add model registry API` 或 `fix: validate skill parameters`。Pull Request 应包含变更摘要、测试证明、关联需求或问题、界面变更截图，以及数据库或配置变更说明。

## 安全与配置建议

不要提交 API Key、模型凭据、数据库密码、大模型密钥或客户水利工程数据。密钥应放在环境变量或密钥管理服务中。示例配置文件使用安全占位符，例如 `LLM_API_KEY=change-me`。
