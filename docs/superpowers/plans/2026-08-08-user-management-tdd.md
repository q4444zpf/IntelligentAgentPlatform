# 通用用户管理增强 TDD 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完善用户、密码、角色、项目授权和会话管理，同时保持 OIDC 统一登录与本地账号登录兼容，并通过服务端权限隔离。

**Architecture:** OIDC 用户由统一认证平台管理密码；本地用户使用 `local_credentials` 和 HttpOnly 会话。所有管理写操作通过服务端会话、单位成员关系和角色校验，开发身份请求头仅限显式 development/test 回环环境。角色变更、密码变更和重置统一提升 `authorization_version`、撤销旧会话并写入审计。

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, PostgreSQL, Vue 3, TypeScript, Vitest, pytest, Docker Compose.

## Global Constraints

- 不保存明文密码、OIDC Token 或数据库密码。
- 生产环境禁止开发身份请求头和本地开发登录适配器。
- 所有 Cookie 会话状态变更必须带 CSRF Token，并通过 Origin 校验。
- 单位管理员只能管理本单位，项目角色只能作用于当前授权项目。
- 内置角色不可删除、不可停用、不可改编码。
- 每个行为变更先写失败测试，确认 RED 后再写最小实现。

---

### Task 1: 修复本地密码后端安全边界

**Files:**
- Modify: `backend/app/identity/admin_router.py`, `backend/app/identity/auth_router.py`, `backend/app/core/request_context.py`, `backend/app/main.py`
- Modify: `backend/app/identity/passwords.py`, `backend/app/identity/models.py`
- Create: `backend/alembic/versions/20260808_12_identity_security.py`
- Test: `backend/tests/identity/test_local_password_api.py`, `backend/tests/identity/test_admin_api.py`, `backend/tests/core/test_request_context.py`

- [ ] 写失败测试：伪造 `X-User-Roles: unit_admin` 不能重置密码；`must_change_password` 账号不能访问业务 API；改密缺少 CSRF/Origin 时失败；停用用户不能登录；异常 PBKDF2 参数被拒绝。
- [ ] 运行 `cd backend; $env:PYTHONPATH='.'; pytest -q tests/identity/test_local_password_api.py tests/identity/test_admin_api.py tests/core/test_request_context.py`，确认上述测试因安全逻辑缺失而 RED。
- [ ] 将管理依赖改为真实 `AuthSession` + 服务端角色/单位关系校验，开发头仅保留回环 development/test 分支。
- [ ] 在认证依赖层增加 `must_change_password` 限制，只放行 `/api/auth/password/change`、`/api/auth/logout` 和必要会话查询。
- [ ] 仅对无会话的登录和 OIDC 回调豁免 CSRF；改密强制 CSRF Token 与 Origin。
- [ ] 增加邮箱规范化唯一约束迁移，并限制密码哈希参数范围。
- [ ] 运行同一测试命令确认 GREEN，再运行 `pytest -q tests/identity`。

### Task 2: 完成本地用户生命周期

**Files:**
- Modify: `backend/app/identity/admin_router.py`, `backend/app/identity/schemas.py`
- Test: `backend/tests/identity/test_local_password_api.py`, `backend/tests/identity/test_admin_api.py`

- [ ] 写失败测试：创建本地用户返回一次性初始密码/邀请状态；重复邮箱返回 `409`；管理员重置后旧会话全部撤销。
- [ ] 运行目标测试确认 RED。
- [ ] 实现创建本地凭据、初始密码仅返回一次、重置密码强制改密和审计事件。
- [ ] 运行目标测试确认 GREEN，并回归 `pytest -q tests/identity`。

### Task 3: 用户角色和项目授权

**Files:**
- Modify: `backend/app/identity/admin_router.py`, `backend/app/identity/schemas.py`, `frontend/src/api/identity.ts`
- Test: `backend/tests/identity/test_admin_api.py`, `frontend/src/api/identity.test.ts`

- [ ] 写失败测试：单位角色和项目角色可分配、移除、替换；跨单位/跨项目操作返回 `403/404`；角色变更提升授权版本并撤销会话。
- [ ] 运行目标测试确认 RED。
- [ ] 增加角色绑定查询、分配、移除和替换接口，复用现有数据库约束。
- [ ] 增加前端 API 类型和请求封装。
- [ ] 运行后端和前端目标测试确认 GREEN。

### Task 4: 用户管理界面接入

**Files:**
- Modify: `frontend/src/views/platform/UserManagementView.vue`
- Modify: `frontend/src/views/platform/IdentityManagementViews.test.ts`
- Create: `frontend/src/views/platform/UserSecurityDialog.vue`

- [ ] 写失败测试：操作栏显示“重置密码”“角色管理”；弹窗提交调用对应 API；OIDC 用户禁用本地密码按钮。
- [ ] 运行 Vitest 确认 RED。
- [ ] 实现资料编辑、重置密码、角色/项目授权弹窗和 loading/error 状态。
- [ ] 运行 `npm run test -- --run src/views/platform/IdentityManagementViews.test.ts` 确认 GREEN。
- [ ] 运行 `npm run build`，确认类型检查和生产构建通过。

### Task 5: 角色管理和会话管理界面

**Files:**
- Modify: `frontend/src/views/platform/RoleManagementView.vue`, `frontend/src/api/identity.ts`
- Create: `frontend/src/views/platform/SessionManagementView.vue`
- Test: `frontend/src/views/platform/IdentityManagementViews.test.ts`, `frontend/src/views/platform/SessionManagementView.test.ts`

- [ ] 写失败测试：角色权限可查看/授权/撤销；非内置角色可编辑/删除；用户会话可查看和撤销。
- [ ] 运行目标测试确认 RED。
- [ ] 实现角色权限和会话管理 UI，保持现有页面样式和权限守卫。
- [ ] 运行前端相关测试和 `npm run build`。

### Task 6: 集成验收和部署

**Files:**
- Modify: `docs/deployment/oidc-development.md`, `frontend/README.md`
- Test: `backend/tests/integration/test_identity_migrations.py`, `tests/e2e/user-management.spec.ts`

- [ ] 写验收测试：OIDC 登录、本地登录、修改密码、管理员重置、强制改密、角色隔离、退出登录。
- [ ] 运行 `docker compose up -d --build api web`。
- [ ] 运行 API 健康检查、后端全量身份测试、前端全量测试和构建。
- [ ] 使用浏览器完成 `/login` -> `/system/users` -> 编辑/重置/角色授权 -> 刷新验证。
- [ ] 检查 `git diff --check`、敏感信息扫描和数据库迁移状态。
