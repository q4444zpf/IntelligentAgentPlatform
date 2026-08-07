# 本地 OIDC 开发与验收

当前没有统一认证平台时，平台继续使用本地开发身份完成业务开发，同时保留正式 OIDC BFF 流程。开发身份只允许在非生产环境启用，不代表统一认证已完成。

## 当前可验证内容

- `POST /api/auth/dev/login`：使用预置开发身份创建 HttpOnly 会话 Cookie。
- `GET /api/auth/me`：从 PostgreSQL 会话和授权数据恢复用户、单位、项目、角色、权限和菜单。
- `POST /api/auth/logout`：撤销会话并清理 Cookie。
- `GET /api/auth/login`：当配置 OIDC Provider 后生成 Authorization Code + PKCE 请求，并设置 `iap_oidc_browser` 浏览器关联 Cookie。
- `GET /api/auth/callback`：校验 state、浏览器关联 Cookie、PKCE、issuer、audience、nonce 和外部身份绑定。

## 本地验证命令

在 `backend` 目录执行：

```powershell
$env:PYTHONPATH='.'
pytest -q tests/identity/test_auth_api.py
```

该测试覆盖会话过期、续期、项目上下文、授权菜单、nonce 和浏览器关联校验。

## 接入 Mock OIDC Provider

配置一个仅用于开发或 CI 的标准 OIDC Provider（例如 Keycloak 或协议级 Mock Provider），然后设置：

```text
OIDC_ISSUER=https://mock-oidc.example.test
OIDC_CLIENT_ID=iap-console
OIDC_CLIENT_SECRET_FILE=.secrets/oidc-client-secret
OIDC_REDIRECT_URI=http://127.0.0.1/auth/callback
IAP_ENVIRONMENT=development
IAP_ALLOW_DEV_IDENTITY=true
IAP_SESSION_COOKIE_SECURE=false
```

Provider 必须注册完全相同的回调地址，并为测试用户预先绑定 `issuer + subject` 到本地用户。浏览器只接收 `iap_session`，不得保存 Access Token、Refresh Token 或 ID Token。

## 切换真实统一认证

真实平台可用后，仅替换 OIDC 配置和 Provider 注册信息，保留本地 PostgreSQL 用户、单位、项目、角色和权限数据。生产环境必须满足：

- 使用 HTTPS 公网地址；
- `IAP_ENVIRONMENT=production`；
- 关闭 `IAP_ALLOW_DEV_IDENTITY`；
- `IAP_SESSION_COOKIE_SECURE=true`；
- 不在前端或日志中暴露 OIDC Token 和 Client Secret。

在真实平台上线前，必须重新执行登录、退出、会话续期、权限同步和跨项目隔离验收。
