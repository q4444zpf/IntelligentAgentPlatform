# 单智能体 Skill Runtime 部署与验收

本手册对应 2026-09-13 单智能体 Skill Runtime 计划。运行身份固定为 Agent 绑定的 `{skill_id, version_id}`，包摘要与文件摘要进入 Run 快照；执行时不会按名称或 `latest` 查找新版。知识库、Embedding、GIS 和多智能体 Team 协议不在本次交付范围内。

## 1. 文件包与声明

ZIP 包包含一个 Skill 根目录，至少有 UTF-8 `SKILL.md`。例如：

```text
forecast/
  SKILL.md
  references/rules.txt
  templates/result.json
  scripts/normalize.py
```

`SKILL.md` 示例：

```yaml
---
name: forecast
description: 水位归一化与结果整理
version: '2.7.0'
metadata:
  scripts:
    - name: normalize
      path: scripts/normalize.py
      timeout_seconds: 5
      requires_approval: false
      input_schema:
        type: object
        properties:
          value: {type: integer}
        required: [value]
        additionalProperties: false
      output_schema:
        type: object
        properties:
          value: {type: integer}
        required: [value]
        additionalProperties: false
---
先读取 references/rules.txt 和 templates/result.json，再调用 normalize，按模板整理结果。
```

清单 `version` 是展示字符串；发布序号是数据库递增整数；`version_id` 才是不可变版本 UUID。三者不能互换。Gateway 通过冻结版本身份、包 SHA-256、归档 SHA-256 和文件 SHA-256 校验，不把展示版本与发布序号比较。

文件路径必须是规范化相对 POSIX 路径。拒绝绝对路径、盘符、反斜杠、`..`、符号链接、特殊文件、重复路径及大小写冲突。包最多 10 MiB，解压总量最多 20 MiB、500 条目；运行资源最多 500 文件、单文件 10 MiB、总计 20 MiB。快照另受 `IAP_RUNNER_SNAPSHOT_MAX_BYTES`（默认 1 MiB）约束；总量不是绕过快照限制的许可。

## 2. 发布与绑定

通过项目 Skill API 导入/编辑草稿，再携带预期 revision 与 `Idempotency-Key` 发布。绑定示例：

```json
{
  "skill_names": ["forecast"],
  "skill_bindings": [
    {
      "skill_id": "ce905a71-a715-48d1-8706-913d91c74932",
      "version_id": "00000000-0000-0000-0000-000000000011",
      "name": "forecast"
    }
  ]
}
```

示例 UUID 必须替换为当前项目实际发布结果。选择版本后，新版发布和草稿修改不会改变原绑定。缺失、停用、未发布版本或只有旧 `skill_names` 的绑定会在模型调用前返回 `skill_unavailable`。

旧配置先盘点项目归属和原名称，再在明确作用域内导入、审核发布，调用 `POST /api/agents/{agent_id}/skill-bindings/migrate` 显式转换。迁移只在用户选择该操作时解析当前发布版本；正常运行绝不做名称回退。保留原目录及历史快照，不推测项目归属，不修改已发布 Team 协议。

项目可用性接口为 `PATCH /api/project-skills/{skill_id}/availability`，请求体例如 `{"enabled": false, "expected_revision": 2}`。重新启用时将 `enabled` 改为 `true`，并传当前最新 `draft_revision`；状态更新会递增 revision，过期 revision 返回 `409 skill_revision_conflict`。需要目标项目 `skill.manage`，Cookie 请求还需通过现有 Origin/CSRF 校验。该接口修改 Skill 资源的当前状态，保留不可变版本、包和正文；响应 `Cache-Control: no-store`，不返回对象存储定位字段。

停用后，新的快照创建与已有快照恢复均重新检查绑定版本的当前可用性并拒绝执行。重新启用恢复原绑定版本，不自动升级。已经送出的模型上下文无法撤回；可用性接口不等同于取消正在执行的 Run，需要终止运行时使用 Run 取消流程。

## 3. Gateway、上下文与脚本

沙箱路径使用真实 `SandboxRuntime -> DeepAgentFactory -> Deep Agents/LangGraph -> GatewayChatModel` 循环。正文与资源索引进入模型上下文；Runner 在专用 workspace 中按清单校验后物化资源，不接收 MinIO 凭据或宿主路径。

每个启用 Skill 暴露 `skill.<name>.resource.read`，只接受 `{path}`，Schema 枚举来自冻结文件清单。工具再次校验 manifest 与返回字节，通过 `RunnerGatewayClient.read_skill_file` 请求 `skill.resource.read` 权限的内部 Gateway。文本以 UTF-8 返回，二进制不能冒充文本。该工具使用与普通工具相同的模型调用预算，不扩大 ArtifactBackend 的读写范围。Deep Agents 自带 filesystem/task 工具可能同时存在。

对象存储读取期间不持有 Run 终态行锁，避免阻塞取消与超时清理。读取完成后，Gateway 在返回字节前锁定并复验 Run 活动状态、token 权限及最新持久快照身份；期间已终态、撤权或快照变化的请求仅记录失败审计，不返回资源。审计显示文本转义控制字符，原始请求路径仅用于计算 SHA-256。

运行预算由快照固定：`IAP_RUNNER_MAX_ITERATIONS` 默认 4、`IAP_RUNNER_MAX_TOOL_CALLS` 默认 8、`IAP_RUNNER_MAX_SUBAGENTS` 默认 4，模型输出默认 4 MiB。多步串行任务应预先配置足够的迭代预算；本手册的自动化验收为五次工具调用加最终回答配置了 8 次迭代上限。

声明脚本暴露为 `skill.<name>.script.<script-name>`。只允许声明的相对 `.py` 路径，不接受 command、shell 或外部可执行文件参数。输入、输出必须是 JSON object，并分别通过声明 Schema；示例脚本：

```python
import json
import sys

value = json.load(sys.stdin)["value"]
json.dump({"value": value + 1}, sys.stdout)
```

Runner 使用 `sys.executable`、`shell=False`，cwd 为该 Skill 的物化目录；环境仅传 `PATH=os.defpath`、`PYTHONIOENCODING=utf-8`、`PYTHONUNBUFFERED=1`，不继承模型令牌、对象凭据或宿主秘密。依赖须预装在受信 Runner 镜像，运行期不自动安装。脚本没有独立网络隔离层，网络边界由 Runner 容器网络策略保证；不得以最小环境代替容器隔离。

`timeout_seconds` 必须为 1 至 120 秒，并受 Run 剩余 deadline 的更小值限制。stdout 和 stderr 分别限制为 1 MiB，stderr 可写诊断但不与 JSON stdout 混合。输入 JSON 通过 Gateway 的有界工具参数合同传入。非零退出、无效 JSON、Schema 错误、超时、取消和输出超限都安全失败，不把原始异常或诊断秘密写入模型错误。

执行前 Gateway 签发持久 `ToolInvocation` 租约。`requires_approval=true` 时进入现有 Approval 流程；批准恢复必须匹配原参数摘要和同一调用身份。重复 admission/completion 使用幂等键，冲突调用拒绝。Runner 接收 SIGTERM 时设置脚本取消事件；Coordinator 先在数据库终态事务中终结运行中的脚本，再撤销 token 并终止容器。Gateway 主动完成 failed/cancelled 同样终结遗留租约。重复和延迟回调不能重写终态或重复终态审计。

内置和 MCP 工具由 Agent 明确绑定，在每次执行时通过 Tool Gateway 检查冻结授权、当前 registry 发布/启用状态、源可用性、参数与审批。MCP endpoint、headers、凭据解析保留在平台侧。Skill 正文或脚本声明不能增加普通工具权限。

非沙箱 `AgentRuntimeHarness` 同样使用冻结正文和资源索引构建上下文，并拒绝无效绑定；本次未向该路径加入宿主脚本执行或本地文件访问。需要 reference/template 实际读取与 declared script 的完整联动时必须使用沙箱 Runner。

## 4. 审计与运维前置

按 `run_id` 查询审计和 RunEvent，可关联 `skill.resource.read/failed`、`skill.script.started/completed/failed/cancelled`、`tool.invoke.started/succeeded/failed`、`llm.invoke.succeeded/failed`、`runner.run.completed/failed/cancelled`。Coordinator 自主管理的终态使用 `sandbox.run.*`。资源审计记录 Skill 名称和相对路径；脚本记录租约、时长和稳定错误码，不记录整个包内容。审批沿用现有 Approval/ToolInvocation 记录。

1. 按 [项目 Skill 生产激活手册](project-skill-production-activation.md) 配置 PostgreSQL、对象存储和受信镜像，显式执行迁移、校验 Alembic revisions、初始化 `IAP_SKILL_BUCKET`，再启用项目 API。
2. Skill availability 迁移为 `20260914_29`，直接依赖 `20260908_27`。若工作区同时有其他独立迁移 head，先核查 `alembic heads/current` 与部署计划，不擅自合并或修改其他业务迁移。本任务不接管控制命令迁移 `20260913_28`。
3. 使用 `IAP_PROJECT_SKILLS_API_ENABLED=true` 启用项目控制面；API 只校验 schema，不在启动时自动迁移或建桶。对象存储失败应关闭对应资源访问，不影响无关 API 健康检查。
4. 为 API 注入 `IAP_RUNNER_TOKEN_SIGNING_KEY` 和内部 `IAP_RUNNER_GATEWAY_URL`，配置 Workflow Runner 和受控 Launcher。Runner 不持有 Docker socket，只有 Launcher 有受控生命周期权限。
5. 在可信镜像、非 root、只读根文件系统、有界 CPU/内存/PID、专用可写临时目录、只允许 Gateway 的网络和可靠清理均验收后，才开启 `IAP_WORKFLOW_RUNNER_SANDBOX_ENABLED` 及相应安全能力开关。不要将宿主工作区、对象存储凭据或任意 host path 挂载给单次 Runner。

## 5. 验收

自动化验收保留真实发布仓储、快照、包验证、Runner Gateway HTTP 路由/令牌、物化、脚本子进程、Deep Agents 图和模型工具循环；仅对象存储、远端 completion 和 MCP transport 为确定性外部边界。单次运行同时验证正文、reference、template、script、内置工具、MCP、最终回答和同 Run 审计。

```text
python -m pytest backend/tests/integration/test_single_agent_skill_tool_mcp_e2e.py -q
python -m pytest backend/tests/runtime backend/tests/skills backend/tests/test_agent_skill_bindings.py -q
python -m pytest backend/tests -q
cd frontend
npm run build
```

浏览器使用本地应用的 `/chat`，选择单智能体，发送新任务，确认最终答复可见并检查同一 Run 的结果；不能把历史会话或静态页面当成本轮执行证据。精确命令、结果、环境限制与截图记录见本次 Task 5 验收报告。
