# OIDC 统一认证与本地授权设计

## 1. 文档定位

本文定义水利智能体平台第一期 Web 控制台和同源嵌入式 Chatbox 的生产认证、会话、单位与项目授权、安全退出、应急访问和测试边界。目标部署形态为单单位、多项目、多角色的一套平台，同时保留后续多单位演进能力。

本文是 `identity` 与 `projects` 领域的权威设计，细化《水利智能体平台总体架构设计》第 9 章，并完整替代《智能体平台详细功能设计与现状改造清单》第 4 章的身份、Token、租户、项目成员、`resource_acl` 和 `/api/auth/refresh` 建议。新实现不得向浏览器签发或暴露 OIDC Access Token、Refresh Token 或 ID Token。

Web BFF Cookie 不能直接用于桌面客户端、跨站嵌入、OpenAPI 或 SDK。桌面客户端后续使用系统浏览器 OIDC + PKCE、受控回调和操作系统凭据库；跨站 Chatbox 使用同源代理或短时嵌入会话；API/SDK 使用独立服务身份和细粒度 scope。它们复用本文的 PostgreSQL 授权服务，但需要单独的认证规范。

## 2. 已确认决策

- 采用 OIDC/OAuth 2.0 Authorization Code Flow + PKCE 接入统一身份平台。
- FastAPI 作为 Backend for Frontend（BFF），负责发起登录、处理回调、交换和校验 Token。
- 浏览器只持有平台生成的 `HttpOnly + Secure + SameSite=Lax` 会话 Cookie。
- OIDC 只负责身份认证；用户、单位、项目、角色、权限及数据范围以 PostgreSQL 为唯一授权事实源。
- 一个用户可以加入多个项目，并在不同项目绑定不同角色。
- 第一阶段不建设 OIDC Provider，不复制参考项目的 OAuth2 服务端，也不引入 Redis 会话架构。
- 保留一个默认关闭、权限固定、不能访问普通业务 API 的本地应急管理员。
- 当前没有真实统一认证平台不阻塞主体开发：CI 使用 Mock OIDC，完整浏览器联调可使用 Keycloak。
- 真实 Provider 允许在 `OidcClient` 边界内增加认证方式或退出适配，但不能改变本地用户、项目和权限模型。

## 3. 范围与非目标

### 3.1 第一阶段范围

- OIDC 登录、回调、身份预绑定、服务端会话、当前用户和本地退出。
- 单位、项目、成员、角色、权限码、菜单映射和数据范围。
- 当前项目切换及后端对象级授权。
- 应急本地管理员、认证审计、CSRF、防暴力登录和会话撤销。
- Mock OIDC 自动化测试、标准 Provider 端到端测试及真实身份平台复验清单。

### 3.2 非目标

- 自建统一认证中心、账号注册、找回密码或普通用户本地密码登录。
- 依赖 OIDC Token 内的角色完成业务授权。
- 第一阶段实现复杂 SaaS 租户计费、跨单位委派或单位自助开通。
- 使用前端菜单、按钮、客户端角色、单位 Header 或项目 Header 作为安全边界。
- 在 Web 页面展示或修改 OIDC Client Secret、会话 Token、应急 TOTP Secret。
- 本文直接实现桌面端、第三方嵌入或服务身份认证。

## 4. 总体架构

```text
浏览器
  ├─ 跳转 OIDC 登录、接收平台会话 Cookie
  ├─ GET /api/auth/me 恢复用户和授权上下文
  └─ Cookie 会话的不安全方法携带 CSRF Header
               ↓
FastAPI BFF
  ├─ OidcClient：Discovery、PKCE、Token 交换与协议校验
  ├─ IdentityBindingService：issuer + sub 映射本地用户
  ├─ SessionService：服务端会话、续期、撤销与 Cookie
  ├─ AuthorizationService：权限与数据范围元组、对象校验
  ├─ ProjectContextService：允许项目查询和当前项目切换
  ├─ EmergencyAuthService：隔离的应急恢复路由
  └─ AuditRecorder：认证、授权和应急操作审计
               ↓
PostgreSQL
  ├─ 用户、外部身份、单位、项目和成员关系
  ├─ 角色、权限、菜单和作用域绑定
  ├─ OIDC 临时事务和服务端会话
  └─ 统一审计事件
               ↓
外部 OIDC Provider
  └─ 只参与认证，不决定本平台业务权限
```

生产环境由 Nginx 提供同源入口。前端静态资源和 `/api` 使用相同站点，降低 Cookie、CORS 和 CSRF 配置复杂度。FastAPI 业务模块只依赖认证后的 `AuthorizationContext`，不得读取浏览器提供的角色或把 OIDC Claims 当作授权结果。

## 5. OIDC 登录与会话

### 5.1 Provider 配置

生产配置至少包括：

- 精确 issuer URL、client ID、client secret 引用、redirect URI 和 scope。
- 连接/读取超时、签名算法白名单及允许时钟偏差。
- Provider 支持的 Client 认证、刷新和退出能力。

除回环测试外必须使用 HTTPS。Discovery 返回的 issuer 必须与配置精确相等；Discovery 和 JWKS 按响应缓存头设置有上限的缓存，遇到未知 `kid` 最多强制刷新一次，仍无法验证时失败关闭。JWT 验证使用成熟 OIDC/JWT 库，禁止自行解析或接受 `alg=none`。

### 5.2 发起登录

`GET /api/auth/login?return_to=/dashboard` 执行：

1. 校验 `return_to` 是站内相对路径，拒绝绝对地址、协议相对地址和控制字符。
2. 生成高熵 `state`、`nonce`、PKCE `code_verifier` 和独立浏览器关联值。
3. 将 `state` 哈希、`nonce` 哈希、浏览器关联值哈希、加密 `code_verifier`、预期 issuer/client/redirect URI、安全回跳路径和五分钟过期时间写入 `oidc_login_transactions`。
4. 把浏览器关联值写入五分钟有效、`HttpOnly`、`Secure`、`SameSite=Lax`、不设置 `Domain` 且 Path 仅限 `/api/auth/callback` 的 `__Secure-iap_oidc_tx` 临时 Cookie。`__Host-` 前缀强制要求 `Path=/`，因此不能用于此路径受限 Cookie。
5. 使用 PKCE S256 跳转授权端点。

登录事务一次性消费。过期、重复使用或不存在的 `state`，以及浏览器关联 Cookie 不匹配时，一律终止流程。这一绑定防止攻击者把自己的回调地址交给其他浏览器造成 Login CSRF。

### 5.3 处理回调

`GET /api/auth/callback` 执行：

1. 处理 Provider 返回的 `code + state` 或 `error + state`。
2. 原子读取并消费登录事务，同时校验 `state`、浏览器关联 Cookie、预期 Provider 参数和过期时间。
3. 由 FastAPI 使用 `code_verifier` 在后端交换 Token。
4. 校验 ID Token 的签名算法白名单、`iss`、`aud`、`azp`、`exp`、`iat`、可选 `nbf` 和 `nonce`。`aud` 为多值时必须包含 client ID 且 `azp == client_id`；时间声明只允许配置的短时钟偏差。
5. 使用 UserInfo 时，再次验证 UserInfo 的 `sub` 与 ID Token 一致。
6. 以经过精确校验的 `issuer + sub` 查询 `external_identities`。不得 lowercase、trim、删除 issuer 尾部字符或执行可能合并两个 issuer 的规范化。
7. 拒绝未绑定、已停用用户或无有效单位成员关系的身份，不自动创建角色和权限。
8. 创建新的平台会话并轮换会话标识。
9. 清除浏览器关联 Cookie，以 HTTP 303 跳回登录事务保存的站内路径。

Token 交换、验证失败或身份未绑定时，回到登录页并携带稳定错误码；不得把 Provider 原始错误、Authorization Code 或 Token 放入后续回跳 URL。

### 5.4 身份预置

第一期不做首次登录自动注册。部署时使用离线管理命令完成以下原子初始化：

1. 创建唯一初始单位和首个本地用户。
2. 从身份平台管理员导出的受信任目录数据取得精确 `issuer/sub`。
3. 创建 `external_identities` 绑定和有效单位成员关系。
4. 为该成员授予内置 `unit_admin`。
5. 写入不含敏感 Claims 的引导审计事件。

后续用户由单位管理员通过受控目录导入或手工输入身份平台管理员确认的 `issuer/sub`。命令和页面都不得按邮箱自动合并身份。若真实 Provider 尚不存在，只能在 Mock/Keycloak 测试环境初始化测试身份，不能因此开放生产 Mock 登录。

### 5.5 Token 最小化

- OIDC Token 全程只存在于 FastAPI 进程和服务端存储。
- 完成 Claims 校验和身份映射后，丢弃不再需要的 Access Token。
- 只有续期、UserInfo 或退出确实需要时，才保存最小 Token 集合。
- 必须保存的 Token 使用 AES-256-GCM 信封加密，密钥和 Key ID 来自环境变量或 Secret Manager。
- Provider 返回新 Refresh Token 时，在同一事务内替换旧密文；明确返回 `invalid_grant` 时撤销平台会话，网络超时则保留仍有效的本地会话并有限重试。
- 加密配置保留当前和上一把解密密钥，读取旧 Key ID 数据时重新加密。
- 浏览器响应、SSE、审计 metadata、异常详情和前端状态中不得出现任何 OIDC Token。

### 5.6 平台会话和项目上下文

生产 Cookie 名称使用 `__Host-iap_session`，设置 `HttpOnly`、`Secure`、`SameSite=Lax`、`Path=/`，不设置 `Domain`。Cookie 值是至少 256 bit 的随机不透明值；PostgreSQL 只保存不可逆哈希。

本地 HTTP 测试可以使用不带 `__Host-` 前缀的测试 Cookie，但只能在显式 `development/test` 环境和回环地址生效。生产环境不能关闭 `Secure`。

默认策略：

- 空闲有效期 30 分钟，绝对有效期 8 小时。
- `last_seen_at` 最多每五分钟更新一次。
- 登录和认证方式切换时轮换会话标识。
- 用户停用、成员关系失效、管理员撤销或上游明确身份失效时立即撤销。
- 暂时性 Provider 网络错误不删除仍有效的平台会话，也不得降级绕过认证。

第一阶段每个普通用户必须恰好有一个有效单位成员关系；没有或出现多个时拒绝建立业务会话。项目上下文按以下规则初始化：

- 没有项目或有多个项目：`current_project=null`，由用户显式选择。
- 只有一个有效项目：可以自动设为当前项目。
- 项目停用或成员关系失效：清空当前项目。
- `current_project=null`：只允许单位级页面和项目选择接口。

实现时必须把 Web 请求入口和业务服务授权统一迁移到 `AuthorizationContext.current_project_id: str | None`，并由每个项目型 API 强制要求项目上下文。旧 `RequestContext.project_id/role/roles` 不保留隐式兼容属性，避免单位级请求把空项目伪装成默认项目，或让服务层继续按角色名称授权。运行时持久化的 `ToolExecutionContext` 仍是独立值对象，继续携带非空项目、会话、运行、时区和角色快照；它只能由已经完成授权的 Conversation/Run 记录构造，不能被 `AuthorizationContext` 替代，也不能反向充当 Web API 授权依据。

### 5.7 本地退出和上游退出

本地退出是必选能力：`POST /api/auth/logout` 首先撤销 PostgreSQL 会话并清除 Cookie，即使 Provider 不可用也必须成功完成本地退出。

上游退出按 Provider 能力分级：

- 支持安全 RP-Initiated Logout 时，使用预登记 `post_logout_redirect_uri` 和一次性 logout state；不得把 ID Token 放入本平台回跳 URL。若 Provider 强制浏览器携带 `id_token_hint`，必须在真实 Provider 安全评审中明确批准，否则只做本地退出。
- 支持 Back-Channel Logout 时提供 `POST /api/auth/oidc/backchannel-logout`。该接口校验 logout token 的签名、issuer、audience、`iat`、`jti` 重放和 OIDC logout events Claim，再按 `sid` 撤销；无 `sid` 时按精确 `issuer/sub` 撤销。
- 不支持上游通知时，身份平台停用用户传播的最坏时延等于平台绝对会话有效期，第一期默认不超过 8 小时；真实 Provider 验收可以要求更短。

前台和后台退出属于 Provider 适配能力，不得用“统一退出已完成”笼统替代本地退出、上游会话和停用传播三项验收。

## 6. 认证 API 与 CSRF

| 方法与路径 | 作用 | 关键要求 |
| --- | --- | --- |
| `GET /api/auth/login` | 发起 OIDC 登录 | 安全 `return_to`、浏览器关联 Cookie |
| `GET /api/auth/callback` | 处理 OIDC 回调 | 一次性事务、PKCE、完整 Token 校验 |
| `GET /api/auth/me` | 恢复用户和授权上下文 | `Cache-Control: no-store`；不返回 OIDC Token |
| `POST /api/auth/logout` | 撤销当前会话 | Cookie 会话 CSRF；本地退出优先 |
| `POST /api/auth/context/project` | 切换当前项目 | 验证成员、角色范围和项目状态 |
| `POST /api/auth/oidc/backchannel-logout` | 接收 Provider 后台退出 | 签名 logout token，不使用浏览器 Cookie CSRF |
| `POST /api/auth/emergency/login` | 应急本地登录 | 默认 404；独立预认证防护和固定路由白名单 |

`GET /api/auth/me` 返回：

```json
{
  "user": {"id": "user-id", "display_name": "用户名称"},
  "unit": {"id": "unit-id", "name": "单位名称"},
  "current_project": {"id": "project-id", "name": "项目名称"},
  "projects": [{"id": "project-id", "name": "项目名称"}],
  "roles": ["project_admin"],
  "permissions": [
    {"code": "agent.read", "target": "current_project"},
    {"code": "project.read", "target": "current_project"}
  ],
  "menus": [
    {
      "kind": "group",
      "key": "resources",
      "title": "资源中心",
      "children": [
        {
          "kind": "route",
          "key": "project-resources",
          "route_key": "project-resources",
          "title": "项目资源"
        }
      ]
    }
  ],
  "authorization_version": 12,
  "csrf_token": "memory-only-token",
  "session": {"idle_expires_at": "2026-08-04T15:30:00+08:00", "absolute_expires_at": "2026-08-04T23:00:00+08:00"}
}
```

`current_project` 可以为空；前端不得把项目列表第一项静默当成授权上下文。`permissions` 是去重并稳定排序的 `(code, target)` 能力对，`target` 只允许 `unit/current_project`；`current_project` 为空时不返回项目目标能力。单位授权在已选择项目时可以同时产生 `unit` 与 `current_project` 能力，但两者不能在前端被拆分或推导。`menus` 只使用上例的严格 `group/route` 判别联合结构，未知字段组合、未知 Key、重复路径或固定路由覆盖都使整棵菜单失败关闭。

每个普通 Web 会话生成独立 CSRF Secret，并在 `auth_sessions` 加密保存。`/api/auth/me` 返回 `base64url(HMAC(csrf_secret, session_internal_id || "csrf-v1"))`；同一会话的多个标签页可以重复取得相同 Token，页面刷新不会使其他标签页立即失效。新会话、会话轮换和撤销会使旧 Token 失效。前端只把 Token 放在内存中，通过 `X-CSRF-Token` 提交。

CSRF 要求只适用于“使用浏览器会话 Cookie 的 POST、PUT、PATCH、DELETE”。服务身份、签名 Webhook、OIDC Back-Channel Logout 使用各自认证和重放防护；应急登录使用严格 Origin、Fetch Metadata、IP、限流、密码和 TOTP，不要求尚不存在的会话 CSRF Token。所有 `/api/auth/*` 敏感响应均设置 `Cache-Control: no-store`。

## 7. 身份和授权数据模型

### 7.1 身份与会话

| 表 | 关键字段和约束 |
| --- | --- |
| `users` | `id`、`display_name`、`email`、`status`、`authorization_version`；不保存普通用户密码 |
| `external_identities` | `user_id`、精确 `issuer`、原始 `subject`、允许列表 Claims、`last_login_at`；`(issuer, subject)` 唯一 |
| `external_identity_history` | 历史已验证 `user_id/issuer/subject`、变更人和时间；只追加，供应急恢复校验 |
| `oidc_login_transactions` | `state_hash`、`nonce_hash`、浏览器关联哈希、预期 Provider、加密 PKCE verifier、回跳、过期和消费字段 |
| `auth_sessions` | 会话哈希、用户、`auth_method`、当前单位/项目、加密 CSRF Secret、授权版本、加密 Token 最小集、`sid`、过期和撤销字段 |
| `emergency_admin_credentials` | 唯一应急用户、Argon2id 密码哈希、加密 TOTP Secret、失败、锁定和轮换字段 |

邮箱、手机号、用户名和显示名只用于展示，不能作为自动身份绑定键。

### 7.2 单位、项目、角色与数据库约束

| 表 | 关键字段和约束 |
| --- | --- |
| `units` | `id`、`code`、`name`、`status`；第一期初始化一个真实单位记录 |
| `projects` | `id`、`unit_id`、`code`、`name`、`status`；`UNIQUE(id, unit_id)` 和 `UNIQUE(unit_id, code)` |
| `unit_memberships` | `user_id`、`unit_id`、`status`；`UNIQUE(user_id, unit_id)` |
| `project_memberships` | `user_id`、`unit_id`、`project_id`、`status`；复合 FK 同时指向单位成员和同单位项目 |
| `roles` | `id`、`code`、`name`、`scope_type`、`unit_id`、`built_in`、`status` |
| `permissions` | 稳定 `code`、资源、动作、风险等级和状态；Code 全局唯一 |
| `role_permissions` | 角色、权限、单位、`data_scope`；同一授权元组唯一 |
| `unit_membership_roles` | 用户、单位、角色和固定 `scope_type=unit` |
| `project_membership_roles` | 用户、单位、项目、角色和固定 `scope_type=project` |
| `role_permission_projects` | 授权元组、单位和项目；仅用于 `custom_projects` |
| `menus` | `kind=group/route`、稳定 `node_key`、可空且唯一的 `route_key`、父节点、标题、排序、状态、`visibility_target=unit/current_project`、`requires_current_project`；不保存可执行组件路径 |
| `menu_permissions` | 路由菜单与权限码映射；与 `visibility_target` 组成导航可见性要求，不作为 API 边界 |

数据库迁移必须落实以下可验收约束：

- `roles`：`platform` 角色的 `unit_id IS NULL`；`unit/project` 角色的 `unit_id IS NOT NULL`。
- 角色绑定表携带固定 `scope_type`，通过 `CHECK` 和 `(role_id, scope_type, unit_id)` 复合 FK 防止把项目角色绑到单位成员。
- `project_memberships(user_id, unit_id)` 复合 FK 指向 `unit_memberships`，`(project_id, unit_id)` 复合 FK 指向 `projects`。
- `role_permission_projects(project_id, unit_id)` 复合 FK 指向同单位项目，并通过授权元组中的 `unit_id` 防止跨单位自定义范围。
- 跨表状态和范围条件由事务内 Service 校验；PostgreSQL 无法用普通 CHECK 表达的规则使用复合 FK 或约束触发器，不能只依赖前端。
- 菜单组必须 `route_key/visibility_target IS NULL` 且 `requires_current_project=false`；路由节点的 `route_key/visibility_target` 必须非空。所有 `visibility_target=current_project` 的路由及单位目标的 `chat` 必须 `requires_current_project=true`，其他单位路由必须为 false；数据库 CHECK 和种子白名单共同阻止组节点伪装路由、重复路径及项目门槛漂移。

第一期初始化 `unit_admin`、`project_admin`、`business_operator`、`model_expert`、`unit_auditor` 和 `viewer`。内置角色不能删除或改 Code；平台范围角色为后续多单位运维保留，第一期页面不能创建或分配。

### 7.3 资源范围

项目型业务资源至少包含：

```text
unit_id, project_id, owner_user_id, created_by, updated_by
```

单位级配置可以允许 `project_id` 为空，但 `unit_id` 必填。Agent、团队、知识库、模型、工作流、工具授权、会话、Run、Artifact 和审计查询必须在 Repository 或 Service 查询中直接加入允许范围。

通用 `resource_acl` 不作为第一阶段默认抽象。不同领域优先使用显式授权表；只有出现稳定、相同的共享语义后再引入通用 ACL。

## 8. 权限计算

### 8.1 权限码

权限码统一使用 `resource.action`，例如：

- `project.read`、`project.manage`、`project.member.manage`
- `agent.read`、`agent.manage`、`agent.run`
- `workflow.read`、`workflow.manage`、`workflow.run`
- `knowledge.read`、`knowledge.manage`、`knowledge.retrieve`
- `model.read`、`model.manage`、`model.run`
- `tool.read`、`tool.manage`、`tool.invoke`
- `mcp.read`、`mcp.manage`、`mcp.sync`
- `sandbox.read`、`audit.read`、`identity.manage`

现有前端 `agent:view` 等冒号格式只作为迁移来源，实施时一次性映射到点号格式。

### 8.2 数据范围和并集语义

| 范围 | 含义 |
| --- | --- |
| `unit` | 角色绑定单位内全部项目和单位级资源 |
| `assigned_projects` | 当前用户有效加入的项目集合 |
| `project` | 当前项目角色绑定的单个项目 |
| `own` | 当前授权边界内由本人拥有或负责的记录 |
| `custom_projects` | 管理员在同单位内明确选择的项目集合 |

项目角色只能使用 `project` 或 `own`；单位角色可以使用 `unit`、`assigned_projects`、`custom_projects` 或 `own`。`own` 永远与角色绑定边界取交集。

授权计算必须以 `(permission_code, scope_predicate)` 为不可拆分元组求并集：

```text
effective_grants =
  UNION(each active role's permission-and-scope tuples)
  ∩ active membership boundaries
  ∩ resource domain authorization
```

禁止先合并权限码、再独立合并所有数据范围。例如：

```text
角色 A: agent.run + own
角色 B: agent.read + unit

正确结果: agent.run 仅 own；agent.read 可 unit
错误结果: agent.run + unit
```

第一期不引入显式拒绝规则。任何并集都不能跨越角色绑定单位。智能体运行等复合操作继续取用户权限、项目范围、资源发布授权、工具/知识/模型授权及沙箱策略的交集。

### 8.3 授权版本和即时生效

- 用户角色、成员、角色权限或项目状态变化时，在同一事务内增加受影响用户的 `authorization_version`。
- API 每次请求从服务端会话取得用户 ID，以 PostgreSQL 当前数据计算或校验授权。
- 会话授权版本落后时重新加载上下文；用户或单位成员停用时直接撤销。
- 前端发现版本变化或收到 403 时，原子清除菜单、路由、缓存和在途请求后重新加载。

## 9. 菜单、按钮、API 和对象权限

- 菜单：后端以 `(permission_code, visibility_target)` 过滤路由节点，只返回闭合白名单内的稳定 `route_key`；组节点无路由且仅在至少一个子节点可见时返回。多个映射权限按“任一完整要求满足”显示，权限码相同但目标范围不匹配仍隐藏。未知组/路由 Key、重复路径或覆盖固定路由时拒绝整个菜单响应，不能部分安装。
- 路由：守卫负责体验，直接输入 URL 仍由目标 API 决定。
- 按钮：前端权限指令只显示或禁用操作，不能代替后端。
- API：FastAPI 统一执行 `require_session` 和 `require_permission(code, target)`；路由准入不能替代实际对象的范围谓词。
- 对象：查询包含 `unit_id/project_id` 和允许范围；具体对象越权返回安全 404，列表和统计不泄露。
- 客户端上下文：生产不得读取单位、项目和角色 Header 生成身份。

当前 Agent、MCP、Tool、LLM Provider 和 Skill 仍是平台全局配置，尚未具备单位/项目/发布授权字段。为防止项目角色通过猜测全局 Agent ID 调用其模型、知识或工具，本阶段 `POST /api/conversations` 和消息提交必须同时满足“已选择项目”与“单位目标的 `agent.run`”。Chat 菜单以 `(agent.run, unit)` 过滤并额外要求当前项目非空；`current_project=null` 时后端不返回 Chat 菜单，前端直接访问 `/chat` 或 `/chat/focus` 都进入项目选择，不能先挂载 Chat 组件再依赖 API 的 409。只有后续 `business-resource-authorization` 为可执行 Agent 建立项目级目录及实际资源谓词后，才能放宽为项目目标。

审计 API 使用专用的“存在 `audit.read` 授权元组”准入，不把项目/own 授权误判成单位授权。列表 SQL 必须把每个 `audit.read` 元组各自编译为谓词后取并集；详情先用同一谓词加载锚点，关联事件也逐条保持同一授权范围。`own` 仅匹配本人的审计事件，未授权详情统一返回 404。

`/`、`/login`、`/select-project`、`/403`、`/404`、`/chat/focus`、`/tenant/resources`、`/system/tenant-projects` 和 catch-all 是前端固定路由，不由数据库菜单创建或删除。`/chat/focus` 仍要求当前项目和单位目标的 `agent.run`；两个旧地址只保留一个版本，目标动态路由未获授权时进入 403。未知 URL 进入 404，权限不足进入 403，二者都不能静默跳回工作台。`current_project=null` 时项目路由进入项目选择页，但已授权单位页面仍可直接访问。

`POST /api/auth/context/project` 只允许切换到有效项目。切换写入服务端会话，增加上下文代次并返回新的 `/api/auth/me` 结构。当前项目是便利上下文，不是授权来源。

## 10. 前端会话状态机

前端不再拥有 Token，也不允许用户在登录页选择角色：

```text
应用启动
→ GET /api/auth/me
→ 已登录：加载用户、项目、权限和菜单
→ 安装静态 route_key 对应路由
→ 优先进入首个已授权单位页面；进入项目页面前必须已有项目上下文，否则进入项目选择

未登录
→ 保存安全站内 return_to
→ GET /api/auth/login
→ OIDC 回调后重新执行启动流程
```

改造约束：

- 删除 Mock Token、角色切换和演示密码登录。
- 用户、权限、项目和 CSRF Token 只保存在 Pinia 内存。
- 401 只触发一次集中清理和登录跳转；403 保持登录并显示无权访问。
- 项目切换取消旧项目请求，清除动态路由、标签页和业务缓存。
- 退出先调用后端撤销，再清除用户、权限、路由、项目和缓存。
- 后端菜单只过滤静态 route key；不能下发任意组件路径。

## 11. 应急本地管理员

### 11.1 开启和网络条件

应急入口必须同时满足：

- `EMERGENCY_LOCAL_LOGIN_ENABLED=true`。
- `EMERGENCY_LOCAL_LOGIN_EXPIRES_AT` 尚未到期。
- 来源 IP 命中 `EMERGENCY_LOCAL_LOGIN_ALLOWED_CIDRS`。
- Argon2id 密码和 TOTP 均验证成功。
- Origin 和 Fetch Metadata 表明请求来自平台同源页面。

默认关闭时返回 404。生产启动时缺少截止时间、允许网段、凭据或 TOTP 时拒绝开放应急登录。

Nginx 必须清除外部传入的 `Forwarded/X-Forwarded-For/X-Real-IP`，并把连接确认的客户端地址覆盖写为单值 `X-Forwarded-For`。Uvicorn 禁用通用代理头重写；应用级客户端地址解析器先校验直接对端属于 `TRUSTED_PROXY_CIDRS`，随后只接受一个语法有效、无逗号链和无重复 Header 的 `X-Forwarded-For`。来自其他对端的 `Forwarded/X-Forwarded-For/X-Real-IP` 一律忽略，可信对端的多值或畸形 XFF 也失败关闭。测试必须同时覆盖不可信对端伪造三类 Header、可信对端单值 XFF，以及可信对端多值/重复 XFF。

### 11.2 身份隔离和固定能力

`auth_method=emergency` 的会话在认证中间件全局拒绝访问普通 `/api` 业务路由，只允许 `/api/auth/emergency/*` 和认证健康检查白名单。不能仅依靠普通权限码阻止业务访问。

允许的专用操作：

- 查看认证健康和脱敏 OIDC 配置。
- 测试 OIDC 连通性。
- 恢复 `external_identity_history` 中已验证且 `user_id/issuer/sub` 完全一致的历史绑定。
- 仅为 `EMERGENCY_RECOVERY_USER_IDS` 中的恢复对象恢复预设 `unit_admin`。
- 撤销异常会话。
- 查看认证与授权审计。

禁止任意 rebind、把同一 `sub` 转给其他用户、创建新恢复对象、访问业务数据、运行智能体/模型/MCP/工具/Shell/沙箱、删除资源或修改自身策略。

### 11.3 凭据和关闭

应急凭据没有默认密码。部署人员使用离线管理命令从交互式安全输入初始化或轮换密码和 TOTP；命令不得接受会进入 Shell 历史的明文参数，也不得记录 TOTP Secret。

登录按账号和 IP 双重限流并递增锁定。每个应急动作使用具体审计动作名并触发告警。统一认证恢复后撤销全部应急会话、关闭开关并轮换密码。

## 12. Web 安全与错误处理

- 生产采用同源部署；携带凭据的 CORS 禁止 `*`。
- 所有使用浏览器会话 Cookie 的不安全方法校验 CSRF Token、Origin 和 Fetch Metadata。
- 登录、回调、`/auth/me` 和错误页设置 `Cache-Control: no-store`。
- 设置 CSP、`frame-ancestors`、HSTS、`X-Content-Type-Options` 和合理 Referrer Policy。
- Token、Cookie、Authorization Header 和密钥字段统一脱敏。
- 生产检测到开发身份 Header、Mock 登录绕过或不安全 Cookie 时拒绝启动。

稳定错误响应：

```json
{
  "code": "AUTH_SESSION_EXPIRED",
  "message": "登录状态已失效，请重新登录",
  "request_id": "trace-id"
}
```

| HTTP | 典型错误 | 行为 |
| --- | --- | --- |
| 401 | `AUTH_REQUIRED`、`AUTH_SESSION_EXPIRED` | 清理状态并进入统一登录 |
| 403 | `AUTH_PERMISSION_DENIED`、`AUTH_IDENTITY_NOT_BOUND` | 不泄露内部策略 |
| 404 | `RESOURCE_NOT_FOUND`、停用应急入口 | 不暴露对象或入口存在性 |
| 409 | `AUTH_CONTEXT_CHANGED` | 重新加载授权上下文 |
| 429 | `AUTH_RATE_LIMITED` | 稍后重试 |
| 503 | `AUTH_PROVIDER_UNAVAILABLE`、`AUTH_SESSION_STORE_UNAVAILABLE` | 失败关闭 |

响应不得包含堆栈、SQL、Provider 原始响应、Token、Client Secret 或完整 Claims。

## 13. 审计兼容与事件契约

现有统一审计实现将 `actor_role` 限定为 `user/project_admin/unit_auditor`，`AuditPolicy` 也按这些角色硬编码。实施本文时必须同步迁移：

- 将角色快照改为长度和元素均受限、排序稳定的 `actor_roles_json`，或等价的独立快照结构。
- 新增稳定 `authorization_scope`，取 `platform/unit/project/own/emergency/system`。
- 审计查询改为 `audit.read` 与其 `(permission, scope predicate)`，不再判断角色名称。
- 新增 `event_scope=platform/unit/project`；`unit_id` 仅在 `platform` 事件允许为空，并用 CHECK 约束。
- 历史事件回填原角色快照和授权范围，现有审计查询测试必须同步迁移。

认证事件使用 `category=security`、`source=auth`。公共字段包括 `status`、`risk_level`、`resource_type/id`、`trace_id` 和唯一 `idempotency_key`。metadata 只允许 `auth_method`、Provider alias、错误码、IP/UA 摘要、`sid` 哈希和上下文版本，不允许 Token、Cookie、密码、TOTP 或完整 Claims。

| 事件 | 状态/风险 | 范围和对象 | 幂等键示例 |
| --- | --- | --- | --- |
| `auth.login.started/succeeded/failed` | started/succeeded/failed；medium/high | platform 或已解析 unit；session/user | `auth:login:{transaction}:{status}` |
| `auth.logout.succeeded/failed` | succeeded/failed；medium | session | `auth:logout:{session}:{status}` |
| `auth.session.revoked/expired` | succeeded；high | session/user | `auth:session:{session}:{action}` |
| `auth.identity.bound/unbound` | succeeded/failed；critical | identity/user | `auth:identity:{request}:{action}` |
| `auth.project.switched/denied` | succeeded/failed；medium | project/user | `auth:project:{request}:{status}` |
| `auth.role.granted/revoked` | succeeded/failed；critical | role/user | `auth:role:{request}:{action}` |
| `auth.permission.changed` | succeeded/failed；critical | role/permission | `auth:permission:{request}:{role}` |
| `auth.emergency.*` | succeeded/failed；critical | 实际恢复对象 | `auth:emergency:{request}:{actual_action}` |

应急事件必须使用实际动作，如 `auth.emergency.identity_binding_restored` 或 `auth.emergency.sessions_revoked`，不能只记录笼统 `action`。登录前事件使用平台范围，不能强行归入默认单位。

## 14. 无真实 Provider 时的测试

### 14.1 强制 Mock OIDC

CI 启动符合 OIDC 的模拟 Provider，生成测试专用签名密钥和 Discovery/JWKS。应用仍走正式 `OidcClient`，不提供直接伪造身份 Header 的旁路。

覆盖正常回调、浏览器关联、state/nonce/PKCE、issuer/audience/算法/时间、JWKS 刷新、UserInfo sub、未绑定身份、停用用户、Token 超时、`invalid_grant`、会话撤销和退出。

### 14.2 标准 Provider 端到端

本地可用 `docker compose --profile oidc-test` 启动 Keycloak。Keycloak 本身不是生产依赖，但生产发布前必须至少通过一个标准 Provider 的真实浏览器端到端测试：真实统一认证平台已可用时直接用真实平台；尚不可用时由 Keycloak 作为发布门槛替代。

### 14.3 真实平台复验

真实 Provider 确定后重新验证 Discovery/JWKS、Client 认证方式（包括可能的 `private_key_jwt`）、响应模式、Claims、证书轮换、刷新、前台/后台退出、用户停用传播和网络策略。适配变化限制在 `OidcClient`、配置和退出适配器，不改变本地授权模型。

## 15. 测试与验收矩阵

| 范围 | 验收结果 |
| --- | --- |
| OIDC | state、nonce、PKCE、算法、issuer、audience、azp 和时间声明完整校验 |
| Login CSRF | state 与浏览器关联 Cookie 一致且一次性消费 |
| 身份 | `(issuer, sub)` 唯一；未绑定身份不自动授权 |
| 浏览器 | Storage、应用 URL、日志和响应中无 OIDC Token |
| Cookie/CSRF | 生产 Cookie 属性正确；多标签页 Token 可恢复；不安全方法拒绝伪造请求 |
| 会话 | 空闲/绝对过期、撤销、用户停用和 `invalid_grant` 正确生效 |
| 多角色 | 按权限与范围元组并集；`run+own` 与 `read+unit` 不提升为 `run+unit` |
| 数据库 | 角色作用域、单位成员、项目成员和自定义范围复合约束拒绝非法数据 |
| 多项目 | 伪造项目或资源 ID 不能横向访问，列表和统计也不泄露 |
| 数据范围 | unit、assigned_projects、project、own、custom_projects 正确过滤 |
| 菜单/API | 菜单按钮匹配权限，直接 URL 和 API 仍由后端拒绝 |
| 全局 Agent 过渡门禁 | 项目级 `agent.run` 即使猜中 Agent ID 也不能创建运行；已选项目加单位级 `agent.run` 才能运行 |
| 项目切换 | 可空初始上下文、有效切换、旧请求和缓存清理 |
| 退出 | 本地会话和前端状态全部失效；按 Provider 能力复验上游退出 |
| 应急 | 默认关闭、可信代理、伪造转发头、TOTP、恢复对象和普通路由拒绝 |
| 审计 | 新角色和应急身份可查询，事件完整且无敏感值 |
| Provider E2E | Keycloak 或真实 Provider 完成浏览器登录、切换和退出 |

## 16. 分阶段落地边界

1. 建立身份、会话、单位、项目、成员、角色和权限迁移，以及 Mock OIDC 测试基础设施。
2. 同步迁移统一审计的固定角色约束和 `AuditPolicy`。
3. 实现 FastAPI OIDC BFF、`AuthorizationContext`、CSRF、认证 API 和错误契约。
4. 为现有 Agent、工具、MCP、模型、会话、Run 和审计 API 接入最小项目及资源授权。
5. 替换前端 Mock 登录、开发身份 Header 和静态权限，接入动态菜单及项目切换。
6. 实现应急恢复白名单、会话撤销、告警及标准 Provider 端到端验收。
7. 真实 Provider 可用后完成适配和专项复验，再开放生产统一登录。

详细文件、测试和提交步骤在本规范经用户书面审阅后单独形成实施计划。本规范阶段不修改认证代码和数据库。

## 17. 旧设计替代映射

| 旧清单第 4 章 | 本文替代方案 |
| --- | --- |
| 浏览器 Access/Refresh Token、`/api/auth/refresh` | BFF 服务端会话；无浏览器刷新接口 |
| `users.password_hash` 普通本地密码 | `users + external_identities`；只有独立应急凭据 |
| `tenants/tenant_members` | 业务概念统一为 `units/unit_memberships` |
| `project_members(project_id, user_id, role_id)` | 项目成员与项目角色绑定分表 |
| 全局 `user-role` | 单位成员角色和项目成员角色分别绑定 |
| 菜单即权限 | `permissions` 独立，`menu_permissions` 只控制导航 |
| 通用 `resource_acl` 首期落地 | 领域显式授权表，稳定后再抽象 |
| 非空前端项目 Header | 可空服务端项目上下文，每次对象访问重新授权 |

已有总体架构中的“用户—角色—项目成员—资源授权”方向保持有效；本文以 `unit` 替代旧清单中的 `tenant`，不在同一实现中维护两套概念。

## 18. 完成标准

- 浏览器不持有任何 OIDC Token，所有生产 Web API 从服务端会话建立身份。
- OIDC 身份与本地用户明确绑定，外部 Claims 不能直接扩展业务权限。
- 单位、项目、角色、权限与数据范围按不可拆分授权元组由 PostgreSQL 强制执行。
- 跨项目读取、统计、运行和管理操作全部失败关闭。
- 菜单、按钮、API 和对象权限职责分离，后端是最终边界。
- 本地退出、权限即时失效、应急恢复和认证审计形成闭环。
- CI Mock OIDC 必须通过；生产前通过 Keycloak 或真实 Provider 浏览器验收。
- 上游退出和用户停用传播按真实 Provider 能力单独验收，不以本地退出替代。
