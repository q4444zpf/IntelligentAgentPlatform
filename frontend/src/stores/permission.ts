import { defineStore } from 'pinia';

import { authApi, type AuthContext, type AuthPermission } from '@/api/auth';
import { setCsrfToken } from '@/api/client';

export type UserRole = 'user' | 'admin';

const userPermissions = [
  'dashboard:view',
  'chat:view',
  'resources:view',
  'resources:personal',
  'resources:public',
  'platform:view',
  'platform:llm',
];

const adminPermissions = [
  ...userPermissions,
  'agent:view',
  'agent:manage',
  'prompt:view',
  'mcp:view',
  'skill:view',
  'tool:view',
  'knowledge:view',
  'workflow:view',
  'collaboration:view',
  'platform:sandbox',
  'integration:view',
  'publish:review',
  'system:view',
  'system:users',
  'system:audit',
  'system:settings',
];

const backendPermissionMap: Record<string, string[]> = {
  'agent.manage:unit': ['agent:view', 'agent:manage'],
  'agent.read:unit': ['agent:view'],
  'agent.run:current_project': ['chat:view'],
  'artifact.read:current_project': ['platform:view'],
  'audit.read:unit': ['system:view', 'system:audit'],
  'collaboration.read:current_project': ['collaboration:view'],
  'conversation.read:current_project': ['platform:view'],
  'credential.read:unit': ['system:view', 'system:settings'],
  'dashboard.read:unit': ['dashboard:view'],
  'identity.read:unit': ['system:view', 'system:users'],
  'integration.read:unit': ['integration:view'],
  'knowledge.read:current_project': ['knowledge:view'],
  'mcp.manage:unit': ['mcp:view'],
  'mcp.read:unit': ['mcp:view'],
  'model.manage:unit': ['platform:llm'],
  'model.read:unit': ['platform:view'],
  'platform.read:unit': ['platform:view', 'dashboard:view'],
  'policy.read:unit': ['system:view', 'system:settings'],
  'project.read:unit': ['system:view', 'system:users'],
  'prompt.read:current_project': ['prompt:view'],
  'resource.publish:current_project': ['resources:view', 'resources:personal'],
  'resource.read:current_project': ['resources:view', 'resources:personal', 'resources:public'],
  'resource.read:unit': ['resources:view', 'resources:public'],
  'resource.review:current_project': ['publish:review'],
  'sandbox.read:unit': ['platform:sandbox'],
  'settings.read:unit': ['system:view', 'system:settings'],
  'skill.manage:unit': ['skill:view'],
  'skill.read:unit': ['skill:view'],
  'tool.manage:unit': ['tool:view'],
  'tool.read:unit': ['tool:view'],
  'workflow.read:current_project': ['workflow:view'],
};

function legacyPermissions(capabilities: AuthPermission[]): string[] {
  const mapped = new Set<string>();
  for (const capability of capabilities) {
    mapped.add(`${capability.code}:${capability.target}`);
    for (const permission of backendPermissionMap[`${capability.code}:${capability.target}`] || []) {
      mapped.add(permission);
    }
  }
  return [...mapped].sort();
}

export const usePermissionStore = defineStore('permission', {
  state: () => ({
    token: localStorage.getItem('iap_token') || '',
    role: 'user' as UserRole,
    permissions: userPermissions,
    userName: '普通演示用户',
    roleName: '普通用户',
    authContext: null as AuthContext | null,
    csrfToken: '',
    sessionRestored: false,
  }),
  getters: {
    isAuthenticated: (state) => Boolean(state.authContext || state.token),
    isAdmin: (state) => state.role === 'admin' || state.authContext?.roles.includes('unit_admin'),
  },
  actions: {
    hasPermission(permission: string) {
      return this.permissions.includes(permission);
    },
    applyAuthContext(context: AuthContext) {
      this.authContext = context;
      this.token = 'cookie-session';
      this.userName = context.user.display_name;
      this.roleName = context.roles.join('、') || '已认证用户';
      this.role = context.roles.includes('unit_admin') ? 'admin' : 'user';
      this.permissions = legacyPermissions(context.permissions);
      this.csrfToken = context.csrf_token;
      setCsrfToken(context.csrf_token);
      this.sessionRestored = true;
      localStorage.removeItem('iap_token');
      localStorage.removeItem('iap_user_role');
      localStorage.removeItem('iap_user_name');
      sessionStorage.removeItem('iap_token');
    },
    clearSession() {
      this.token = '';
      this.authContext = null;
      this.csrfToken = '';
      setCsrfToken('');
      this.sessionRestored = true;
      this.switchRole('user');
      localStorage.removeItem('iap_token');
      localStorage.removeItem('iap_user_role');
      localStorage.removeItem('iap_user_name');
      sessionStorage.removeItem('iap_token');
    },
    async refreshSession() {
      const context = await authApi.me();
      this.applyAuthContext(context);
      return context;
    },
    async restoreSession() {
      if (this.sessionRestored) return;
      try {
        await this.refreshSession();
      } catch {
        this.clearSession();
      }
    },
    async loginWithDevelopmentIdentity() {
      await authApi.devLogin();
      await this.refreshSession();
    },
    async loginWithLocalCredentials(email: string, password: string) {
      const result = await authApi.localLogin({ email, password });
      await this.refreshSession();
      return result;
    },
    async startOidcLogin() {
      const { authorization_url: authorizationUrl } = await authApi.login();
      window.location.assign(authorizationUrl);
    },
    switchRole(role: UserRole) {
      this.role = role;
      if (role === 'admin') {
        this.permissions = adminPermissions;
        this.userName = '平台管理员';
        this.roleName = '管理员';
        return;
      }

      this.permissions = userPermissions;
      this.userName = '普通演示用户';
      this.roleName = '普通用户';
    },
    login(payload: { username: string; role: UserRole; remember: boolean }) {
      this.token = `mock-token-${Date.now()}`;
      this.authContext = null;
      this.csrfToken = '';
      this.sessionRestored = true;
      this.switchRole(payload.role);
      this.userName = payload.role === 'admin' ? '平台管理员' : payload.username || '普通演示用户';

      if (payload.remember) {
        localStorage.setItem('iap_token', this.token);
        localStorage.setItem('iap_user_role', payload.role);
        localStorage.setItem('iap_user_name', this.userName);
      } else {
        sessionStorage.setItem('iap_token', this.token);
      }
    },
    async logout() {
      try {
        if (this.authContext) await authApi.logout();
      } finally {
        this.clearSession();
      }
    },
  },
});
