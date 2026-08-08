# OIDC Mock 联调设计

## 目标

在没有统一认证平台的情况下，提供一个仅用于开发和测试的协议级 Mock OIDC Provider，验证平台 OIDC BFF 的完整链路；统一认证平台上线后，仅替换配置和 Provider 注册信息，不改变平台用户、单位、项目、角色和权限数据。

## 边界

- Mock Provider 只允许在 `development`/`test` 环境启用，禁止生产启动加载。
- 平台使用 Authorization Code + PKCE，校验 discovery、issuer、state、nonce、PKCE、audience、azp、时间窗口和一次性授权码。
- OIDC Access Token、Refresh Token、ID Token 只存在服务端内存或服务端请求过程，浏览器只接收 HttpOnly 会话 Cookie 和必要的 CSRF 状态。
- 回调成功后按外部 `issuer + subject` 绑定或查找平台用户，创建平台服务端会话并同步权限，不根据外部角色直接授予平台权限。
- 退出登录撤销平台会话并清理 Cookie；续期仅更新平台会话，不把 Refresh Token 下发浏览器。

## 组件与接口

- `backend/tests/support/mock_oidc_provider.py`：测试用 Provider，提供 discovery、authorize、token、JWKS 和可控 claims。
- `backend/app/identity/oidc.py`：Provider discovery、授权码交换、ID Token 严格校验。
- `backend/app/identity/auth_router.py`：`/api/auth/login`、`/api/auth/callback`、`/api/auth/logout`、`/api/auth/me`。
- `backend/tests/identity/test_oidc_client.py`：协议校验单元测试。
- `backend/tests/integration/test_oidc_mock_flow.py`：登录、回调、会话、退出和权限同步集成测试。

## 验收标准

1. 未配置 OIDC 时，本地开发身份仍可用，OIDC 登录返回明确的未配置响应。
2. Mock OIDC 正常流程可以建立平台会话并通过 `/api/auth/me` 返回数据库权限。
3. state、nonce、PKCE、issuer、audience、azp、过期时间、重放授权码和错误 token 均被拒绝。
4. 浏览器 Cookie 不包含 OIDC Token；退出或权限变更后平台会话失效。
5. 后端身份测试、OIDC 集成测试和前端认证测试全部通过。
