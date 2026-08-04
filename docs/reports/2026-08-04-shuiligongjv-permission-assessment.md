# ShuiLiGongJv 权限管理参考项目评估报告

## 1. 评估信息

| 项目 | 内容 |
| --- | --- |
| 评估日期 | 2026-08-04 |
| 后端根目录 | `E:/project/建模平台/ShuiLiGongJv/server` |
| 前端根目录 | `E:/project/建模平台/ShuiLiGongJv/web` |
| 方法 | 只读代码审计，不运行、不修改参考项目 |
| 版本标识 | 上述目录及上级 `E:/project` 未提供 `.git`，commit 和 branch 无法取得 |
| 证据有效性 | 行号基于 2026-08-04 本地快照；源文件变化后需重新核对 |
| 分发级别 | 内部受限；包含未修复安全风险位置，修复前不建议公开分发 |

报告中的 `SERVER/` 和 `WEB/` 分别代表上述根目录。配置中发现的密码、Token、Client Secret 和 API Key 不在本文展示，也未验证其当前有效性。

目标平台当前范围是单单位、多项目、多角色，采用 OIDC 统一认证和 PostgreSQL 本地授权。当前没有真实统一认证平台，因此同时评估可测试性和替代测试方案。

## 2. 严重度定义

本报告的 P0/P1/P2 是“参考实现能否进入目标平台”的落地优先级，不等同于 CVSS：

| 级别 | 判定标准 | 处理要求 |
| --- | --- | --- |
| P0 上线阻断 | 默认或常见配置可达的认证绕过、匿名高风险操作、敏感凭据暴露，或项目核心隔离边界缺失 | 目标平台上线前必须消除，禁止直接迁移 |
| P1 高风险 | 通常需要已认证用户、特定配置或已有立足点，但可造成越权、凭据扩大暴露或会话安全失效 | 进入生产前修复并建立回归测试 |
| P2 中风险 | 测试、可维护性、失效传播或纵深防御不足，本身不直接证明可利用漏洞 | 纳入实施计划和发布门禁 |

风险项分别说明可达条件和影响。配置中出现的凭据因未做有效性验证，按“泄露处置优先级 P0”处理，不宣称当前仍可被使用。

## 3. 执行结论

参考项目具备成熟管理后台常见的用户、角色、菜单、权限码、部门数据范围、动态路由和日志模块。这些领域划分与交互模式可以借鉴。

参考项目不能作为目标平台认证实现直接迁移。其核心认证是自建 opaque Token，不是 OIDC Client；前端长期保存 Access Token 和 Refresh Token；刷新、退出、匿名路由和模拟登录存在高风险。部门数据范围也不能表达目标平台的项目成员、资源授权、智能体运行和跨项目隔离。

结论：

- **借鉴** RBAC、权限码、动态菜单、方法级守卫、数据范围和审计分层思想。
- **重构** 用户、单位、项目成员、作用域角色和授权上下文。
- **拒绝移植** 自建 OAuth2/Token、LocalStorage Token、查询串 Refresh Token、客户端租户边界、模拟登录和宽松匿名路径。
- **目标方案** Web 使用 FastAPI OIDC BFF 和安全 Cookie；PostgreSQL 是授权唯一事实源。

## 4. 参考架构摘要

### 4.1 后端

- Java 11、Spring Boot 2.7.18 的 Maven 模块化单体。[B01]
- Spring Security 使用无状态请求，关闭 CSRF，认证过滤器从自建 Token 服务恢复用户。[B02]
- Access Token 和 Refresh Token 均为随机 opaque 值并保存数据库；只有 Access Token 另缓存 Redis。[B03]
- 权限使用 `@PreAuthorize("@ss.hasPermission('permission-code')")` 形式，由用户角色和角色菜单查询权限码。[B04]
- 数据范围围绕部门树和本人数据，通过 MyBatis SQL 拦截器追加条件。[B05][B06]
- 框架包含租户上下文和 SQL 租户拦截器，但主配置关闭多租户。[B07]

### 4.2 前端

- Vue 管理后台使用 Pinia 保存用户、角色和权限，后端返回菜单后动态安装路由。[F03][F04]
- 默认把 Access Token、Refresh Token、用户和角色写入持久缓存。[F05][F14]
- `v-auth`、表格操作和列级控制复用 permission code。[F11][F12]
- `/sso` 主要用于本系统作为 OAuth2 授权服务器时的授权确认，不是 OIDC Client 登录回调。[F02]
- 未发现覆盖登录、刷新、退出、动态路由和越权访问的常规前端自动化测试。

## 5. 可借鉴能力

### 5.1 用户、角色和权限码

参考项目形成用户、角色、用户角色、角色菜单关系。角色包含状态和数据范围，权限查询排除停用角色，并在角色或菜单关系变化后清理缓存。[B04][B22][B23]

目标平台可以借鉴：

- 用户、角色、权限和关联表分离。
- 稳定权限码用于前后端一致表达操作。
- 角色启停、内置角色保护和变更后失效。
- 后端集中返回当前用户、角色、权限和菜单。

需要调整：

- 角色绑定必须具有单位或项目范围。
- 权限码不能依附菜单存在；无菜单 API、工具调用和后台任务同样需要权限。
- OIDC Claims 不能替代本地角色绑定。

### 5.2 方法级守卫

`@PreAuthorize` 和集中权限服务体现“声明权限、集中计算”的正确方向。[B04] FastAPI 中应对应为 `require_permission("workflow.run", project_id)`，并在 Service/Repository 再次按资源 `unit_id/project_id` 过滤。只有方法级权限而没有对象范围仍会横向越权。

### 5.3 数据范围

源码枚举是 `ALL/DEPT_CUSTOM/DEPT_ONLY/DEPT_AND_CHILD/SELF`，多个角色的数据范围在服务层汇总，SQL 拦截器在无权限时返回空结果。[B04][B05][B06]

目标平台改为 `unit/assigned_projects/project/own/custom_projects`。第一期以 Service/Repository 显式范围过滤、复合外键和约束为主。PostgreSQL RLS 是否增加由威胁模型、连接池上下文和运维成熟度另行决定，不把它当作容量优化，也不把它作为第一期正确性的替代。

### 5.4 动态菜单与按钮

参考前端从后端权限信息生成菜单和动态路由，按钮、表格操作及列使用 permission code。[F04][F08][F11][F12]

目标平台必须收紧：

- 后端只返回稳定 `route_key`，前端从静态注册表映射组件。
- 后端不能下发任意组件路径。
- 菜单、路由和按钮只用于体验；API 和对象授权由后端执行。
- 项目切换后原子重载菜单、权限、路由、标签页和缓存。

### 5.5 审计分层

参考后端具备登录日志、API 访问日志和操作日志，可记录 trace、IP、User-Agent、结果和耗时。[B25][B26] 目标平台应统一保留并贯通 `trace_id`，增加 OIDC `issuer/sub`、本地用户、单位、项目、授权决定、资源和认证方式，并沿用现有统一审计中心的追加式和脱敏规则。

## 6. 不能照搬的设计

| 参考实现 | 风险 | 目标处理 |
| --- | --- | --- |
| 自建 OAuth2 风格 opaque Token | 协议、轮换、撤销由业务代码承担 | 标准 OIDC Provider 和成熟客户端库 |
| Token 存 LocalStorage | XSS 可读取；前端内置加密密钥不能形成安全边界 | Web 只持有 HttpOnly 会话 Cookie |
| 查询串提交 Token/Refresh Token | 暴露于代理、网关、APM、访问日志和网络诊断 | Token 只在 BFF 后端通道使用 |
| 客户端提交 `tenant-id` | 可伪造，不能证明成员关系 | 服务端会话和 PostgreSQL 成员关系 |
| 菜单同时承担 API 权限 | 导航和安全耦合 | `permissions` 与 `menus` 独立 |
| Redis Token 架构 | 第一阶段增加组件和运维成本 | PostgreSQL 服务端会话 |
| 前端隐藏敏感路由 | 可直接访问 API 或 URL | API 和对象级失败关闭 |
| 普通本地账号密码 | 与统一身份生命周期形成双轨 | 只保留受限应急账号 |
| 上游角色直接授权 | Claims 变化可扩大业务权限 | OIDC 只认证，本地角色授权 |

## 7. 主要风险

### 7.1 P0：默认开发配置可启用模拟身份

主配置默认激活 dev profile，dev 配置开启模拟登录；默认模拟密钥是固定值，过滤器允许按“密钥前缀 + 用户 ID”构造身份。[B07][B08][B11][B12]

**可达条件：** 使用仓库默认 profile 且未覆盖 Mock 配置。
**影响：** 可选择任意用户身份，形成认证绕过。
**目标处理：** 生产启动检测到 Mock、开发身份 Header 或不安全 Cookie 时直接失败。

### 7.2 P0：项目成员和对象边界缺失

数据集和训练任务接口直接接受项目编号，未发现项目成员关系或资源 ACL。部门数据权限只注册用户和部门，不能覆盖 dataset、model 和 task。[B16][B17][B18]

**可达条件：** 攻击者已登录并能调用相关接口。
**影响：** 认证用户之间可能发生项目横向越权。
**分级说明：** 虽非匿名漏洞，但项目隔离是目标平台核心安全边界，因此作为上线阻断 P0。

### 7.3 P0：配置中存在凭据位置

多个环境配置文件包含数据库、消息队列、第三方客户端、对象存储或镜像仓库凭据位置。[B08][B09][B10]

**可达条件：** 能读取仓库、构建物或配置分发副本。
**影响：** 若值仍有效，可扩大到外部基础设施。
**核验状态：** 未验证凭据有效性，不复述任何值；按已泄露执行轮换、清理历史和密钥托管。

### 7.4 P0：存在条件性匿名模型运行入口

安全配置和业务控制器存在 `permitAll/@PermitAll` 路径，其中模型注册控制器可匿名触发模型运行。[B02][B20][B21]

**可达条件：** 对应模块已部署、路由可从外网访问且没有额外网关保护。
**影响：** 未认证调用可能消耗资源或触发业务执行。
**目标处理：** 逐项清点匿名端点；内部回调改用服务身份、签名和重放防护。

### 7.5 P1：自定义业务权限拦截失效

实际使用 `@Permission` 的模型控制器未标注对应控制器级注解；拦截器存在直接放行路径，核心校验被注释，异常被吞掉后继续执行。[B19][B20]

**可达条件：** 攻击者已认证且能访问对应控制器。
**影响：** 绕过业务操作级权限。
**目标处理：** 不迁移该拦截器；所有接口使用统一 FastAPI 权限依赖和对象过滤。

### 7.6 P1：Token 暴露面过大

- Token 可从请求查询参数读取。[B13]
- Refresh Token 通过查询串提交。[B14]
- Access Token 明文值保存数据库和 Redis；Refresh Token 明文值保存数据库。[B03]
- 认证过滤器记录完整 Token 校验结果 DTO；该 DTO 不含 Token，但包含用户、租户、scope 和过期时间，扩大认证元数据日志暴露。[B12][B15]

### 7.7 P1：前端刷新链路发送错误凭据

刷新成功后保存新 Access Token，却把旧 Refresh Token 放进当前和排队业务请求的 `Authorization`。[F06]

**影响：** 请求失败，并把高价值刷新凭据发送到普通业务接口。

### 7.8 P1：前端认证状态不完整

- 退出未可靠清理 Refresh Token、持久角色、动态路由和全部缓存。[F03][F09]
- 登录页硬编码开发凭据；报告不复述具体值。[F01]
- 回车路径直接调用登录处理，绕过前端验证码入口。[F01]
- 登录响应 `expiresTime` 未用于会话过期或预刷新。[F13]
- `/sso` 不在匿名白名单，重定向仅保存 path，可能丢失 OAuth 事务查询参数。[F02][F08]

### 7.9 P1：匿名和基础路由范围过宽

后端整体匿名开放 `/app-api/**`，Swagger、Actuator、Druid 等存在匿名配置；前端部分敏感页面进入基础路由，业务白名单也较宽。[B02][B21][F08][F10]

`permitAll` 配置不等于每个端点在当前部署都可外网访问，生产前仍需结合模块、网关和网络路径逐项验证。

### 7.10 P1：密码与暴力破解防护偏弱

参考本地密码使用 BCrypt，但默认 cost 较低；密码长度策略较短，多个环境关闭验证码，未定位到完整账户级失败锁定。目标平台普通用户不保留本地密码；应急账号采用 Argon2id、TOTP、IP 限制和双重限流。

### 7.11 P1：租户模型不能替代项目权限

主配置关闭租户能力；部分关联表和令牌表没有租户字段。[B07][B22][B23][B24] 如果参考部署本来就是单租户，这不单独证明现役越权，但它阻断目标平台直接迁移，也不能替代项目成员和对象授权。

### 7.12 P2：认证授权回归测试不足

与认证授权直接相关的后端测试主要覆盖数据权限框架和部门规则；未形成 OIDC、跨项目 IDOR、Mock 禁用、Token 脱敏、角色失效和退出清理的系统测试。前端未发现相应常规自动化测试。

## 8. 当前智能体平台差距

当前项目尚未建立生产认证边界：

- `frontend/src/views/auth/LoginView.vue` 接受演示账号和任意密码，并允许选择角色。[T01]
- `frontend/src/stores/permission.ts` 在 remember 分支持久化 Mock Token、角色和用户名；SessionStorage 分支只保存 Token。权限数组不持久化，而是由客户端角色重新派生。[T02]
- `frontend/src/api/client.ts` 在显式环境变量存在时发送单位、用户、项目和角色开发 Header。[T03]
- `backend/app/core/request_context.py` 只有显式 `allow_dev_identity` 模式，没有生产身份认证；该开关默认关闭。[T04]
- 当前菜单和路由是静态定义，守卫只检查客户端权限，无权时回首页，尚无后端菜单过滤、专用 403 页面和授权状态原子重载。[T05][T06]

这些机制适合演示和受控测试，不具备生产安全性。迁移时必须整体替换，不能只增加“已登录”布尔值。

旧清单第 4 章提出前端 Access/Refresh Token、`tenant` 和通用 `resource_acl`；这些内容由 `docs/superpowers/specs/2026-08-04-oidc-local-authorization-design.md` 完整替代。总体架构的用户、角色、项目成员和资源授权方向保持有效。

## 9. 目标认证边界

| 使用方 | 认证方式 | 本地授权 |
| --- | --- | --- |
| Web 控制台 | FastAPI BFF + OIDC + HttpOnly Cookie | PostgreSQL 单位/项目/角色/范围 |
| 同源 Chatbox | 复用站点 BFF 会话 | 与当前用户和项目取交集 |
| 跨站嵌入 Chatbox | 同源代理或短时受控嵌入会话，另行设计 | 服务器签发的受限上下文 |
| 桌面客户端 | 系统浏览器 OIDC + PKCE、受控回调和系统凭据库，另行设计 | 复用同一授权服务 |
| OpenAPI/SDK | 服务身份、细粒度 scope，可结合 mTLS，另行设计 | 服务主体和项目资源授权 |

Web `SameSite=Lax` Cookie 不复制到桌面端，也不作为第三方跨站嵌入方案。

## 10. 目标适配方案

| 目标能力 | 采用方式 |
| --- | --- |
| 统一认证 | FastAPI BFF + OIDC Authorization Code + PKCE |
| 浏览器会话 | `HttpOnly + Secure + SameSite=Lax` 不透明 Cookie |
| 身份映射 | 精确 `(issuer, sub)` 绑定本地用户 |
| 授权事实源 | PostgreSQL 用户、单位、项目、成员、角色和权限 |
| 多角色 | 按 `(permission, scope predicate)` 元组求并集，不跨单位 |
| 数据范围 | unit、assigned_projects、project、own、custom_projects |
| 菜单路由 | 后端过滤 route key，前端静态组件注册表 |
| API 和对象 | FastAPI 权限依赖 + Service/Repository 范围过滤 |
| 会话失效 | 服务端撤销 + authorization_version |
| 应急访问 | 单一、默认关闭、限时、可信代理、密码+TOTP、专用路由 |
| 审计 | PostgreSQL 追加式统一审计中心 |

初始内置角色建议为单位管理员、项目管理员、业务操作员、模型专家、单位审计员和只读用户。应急管理员不属于普通角色体系。

## 11. 没有统一认证平台时的影响

不会阻塞用户、项目、角色、菜单、API、数据范围、会话和应急能力的开发测试，但不能完成特定 Provider 的最终联调。

测试分层：

1. CI 启动 Mock OIDC Provider，应用仍执行正式 Discovery、JWKS、PKCE、回调和 Token 校验。
2. 本地可通过 Docker Compose Profile 启动 Keycloak，验证真实浏览器跳转和 Cookie 会话。
3. 生产前至少通过 Keycloak 或真实 Provider 之一的端到端验收。
4. 真实 Provider 确定后复验 Claims、Client 认证、证书轮换、退出、刷新、停用传播和网络策略。

Keycloak 只用于测试，不进入第一阶段生产拓扑。应急管理员不能替代普通功能测试。

## 12. 落地优先级

### P0：建立安全边界

- OIDC BFF、服务端会话、CSRF 和稳定错误契约。
- 用户、外部身份、单位、项目、成员和作用域角色。
- 所有当前可调用 Agent、Tool、MCP、模型、知识库、会话和 Run 的最小项目及资源授权。
- 后端对象过滤、安全 404 和跨项目回归。
- 替换 Mock Token、角色选择、开发身份 Header 和纯客户端守卫。
- 认证与授权审计及现有固定角色审计策略迁移。

### P1：形成可运营闭环

- 角色权限管理页面、后端菜单过滤、动态路由、403 页面和项目切换。
- 应急恢复白名单、会话撤销和安全告警。
- 高级资源分享、批量授权和管理体验。
- Keycloak 或真实 Provider 浏览器端到端验收。

### P2：真实平台与纵深防御

- 真实 Provider 的特殊 Client 认证、退出和证书轮换演练。
- 受控外部用户组同步，不直接授予权限。
- 根据威胁模型和运维成熟度评估 PostgreSQL RLS。
- 有多实例需求后评估会话缓存和失效广播。
- 多单位管理、平台运维角色和跨单位审计另行设计。

## 13. 证据索引

### 13.1 后端证据

| ID | `SERVER/` 相对路径 | 行 |
| --- | --- | --- |
| B01 | `pom.xml` | 29-38 |
| B02 | `hbsd-framework/hbsd-spring-boot-starter-security/src/main/java/com/hbsd/_3dsy/framework/security/config/HbsdWebSecurityConfigurerAdapter.java` | 109-150 |
| B03 | `hbsd-module-system/hbsd-module-system-biz/src/main/java/com/hbsd/_3dsy/module/system/service/oauth2/OAuth2TokenServiceImpl.java` | 101-171 |
| B04 | `hbsd-module-system/hbsd-module-system-biz/src/main/java/com/hbsd/_3dsy/module/system/service/permission/PermissionServiceImpl.java` | 64-110, 273-326 |
| B05 | `hbsd-framework/hbsd-spring-boot-starter-biz-data-permission/src/main/java/com/hbsd/_3dsy/framework/datapermission/core/rule/dept/DeptDataPermissionRule.java` | 90-160 |
| B06 | `hbsd-module-system/hbsd-module-system-api/src/main/java/com/hbsd/_3dsy/module/system/enums/permission/DataScopeEnum.java` | 18-26 |
| B07 | `hbsd-server/src/main/resources/application.yaml` | 6-7, 247-250, 276-278 |
| B08 | `hbsd-server/src/main/resources/application-dev.yaml` | 53-72, 227-230 |
| B09 | `hbsd-server/src/main/resources/application-linux.yaml` | 49-56, 200-203 |
| B10 | `hbsd-server/src/main/resources/application-local.yaml` | 67-72, 220-223 |
| B11 | `hbsd-framework/hbsd-spring-boot-starter-security/src/main/java/com/hbsd/_3dsy/framework/security/config/SecurityProperties.java` | 34-50 |
| B12 | `hbsd-framework/hbsd-spring-boot-starter-security/src/main/java/com/hbsd/_3dsy/framework/security/core/filter/TokenAuthenticationFilter.java` | 73-120 |
| B13 | `hbsd-framework/hbsd-spring-boot-starter-security/src/main/java/com/hbsd/_3dsy/framework/security/core/util/SecurityFrameworkUtils.java` | 40-52 |
| B14 | `hbsd-module-system/hbsd-module-system-biz/src/main/java/com/hbsd/_3dsy/module/system/controller/admin/auth/AuthController.java` | 84-89 |
| B15 | `hbsd-module-system/hbsd-module-system-api/src/main/java/com/hbsd/_3dsy/module/system/api/oauth2/dto/OAuth2AccessTokenCheckRespDTO.java` | 16-41 |
| B16 | `exerunner-master/src/main/java/com/hbsd/_3dsy/module/controller/admin/prediction/DatasetController.java` | 38, 53-55 |
| B17 | `exerunner-master/src/main/java/com/hbsd/_3dsy/module/controller/admin/prediction/TrainTaskController.java` | 117-134 |
| B18 | `hbsd-module-system/hbsd-module-system-biz/src/main/java/com/hbsd/_3dsy/module/system/framework/datapermission/config/DataPermissionConfiguration.java` | 18-24 |
| B19 | `hbsd-module-product/src/main/java/com/hbsd/_3dsy/module/wmp/filter/PermissionInterceptor.java` | 47-88 |
| B20 | `hbsd-module-product/src/main/java/com/hbsd/_3dsy/module/wmp/controller/admin/ModelRegisterRecordController.java` | 相关 `@Permission`；251-257 匿名运行入口 |
| B21 | `hbsd-module-infra/hbsd-module-infra-biz/src/main/java/com/hbsd/_3dsy/module/infra/framework/security/config/SecurityConfiguration.java` | 26-39 |
| B22 | `hbsd-module-system/hbsd-module-system-biz/src/main/java/com/hbsd/_3dsy/module/system/dal/dataobject/permission/UserRoleDO.java` | 15-33 |
| B23 | `hbsd-module-system/hbsd-module-system-biz/src/main/java/com/hbsd/_3dsy/module/system/dal/dataobject/permission/RoleMenuDO.java` | 15-33 |
| B24 | `hbsd-module-system/hbsd-module-system-biz/src/main/java/com/hbsd/_3dsy/module/system/dal/dataobject/oauth2/OAuth2RefreshTokenDO.java` | 27-36 |
| B25 | `hbsd-module-system/hbsd-module-system-api/src/main/java/com/hbsd/_3dsy/module/system/api/logger/dto/OperateLogCreateReqDTO.java` | 17-22 |
| B26 | `hbsd-module-system/hbsd-module-system-biz/src/main/java/com/hbsd/_3dsy/module/system/dal/dataobject/logger/LoginLogDO.java` | 38-40 |

### 13.2 前端证据

| ID | `WEB/` 相对路径 | 行 |
| --- | --- | --- |
| F01 | `src/views/base/login/LoginForm.vue` | 53-84, 102-146 |
| F02 | `src/views/base/login/SSOForm.vue` | 29-115 |
| F03 | `src/store/modules/user.ts` | 21-191 |
| F04 | `src/store/modules/permission.ts` | 207-243 |
| F05 | `src/utils/auth/index.ts` | 6-45 |
| F06 | `src/utils/http/axios/Axios.ts` | 51-54, 111-149 |
| F07 | `src/utils/http/axios/index.ts` | 76-97, 182-193 |
| F08 | `src/router/guard/permissionGuard.ts` | 13-129 |
| F09 | `src/router/guard/stateGuard.ts` | 10-21 |
| F10 | `src/router/routes/index.ts` | 86-180, 345-355 |
| F11 | `src/hooks/web/usePermission.ts` | 62-83 |
| F12 | `src/directives/permission.ts` | 10-29 |
| F13 | `src/api/base/model/userModel.ts` | 23-45 |
| F14 | `src/settings/projectSetting.ts` 和 `src/settings/encryptionSetting.ts` | 33-39；6-13 |

### 13.3 当前目标平台证据

| ID | 仓库相对路径 | 行 |
| --- | --- | --- |
| T01 | `frontend/src/views/auth/LoginView.vue` | 43-75, 104-145 |
| T02 | `frontend/src/stores/permission.ts` | 37-94 |
| T03 | `frontend/src/api/client.ts` | 1-15 |
| T04 | `backend/app/core/request_context.py` | 13-75 |
| T05 | `frontend/src/router/routes.ts` | 14-96, 101-152 |
| T06 | `frontend/src/router/index.ts` | 27-32 |

## 14. 评估结论

参考项目适合作为后台权限交互和 RBAC 思路来源，不适合作为认证代码基线。目标平台应保留其用户角色关系、权限码、动态菜单、数据范围和审计分层，同时用标准 OIDC BFF、服务端会话、项目成员模型和对象级授权替换现有 Token、租户和项目边界。

即使统一认证平台尚未建设，也可以完成主体功能和安全回归；上线前仍必须对真实 Provider 执行专项复验。
