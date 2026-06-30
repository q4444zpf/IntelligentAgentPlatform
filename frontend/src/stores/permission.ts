import { defineStore } from 'pinia';

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

export const usePermissionStore = defineStore('permission', {
  state: () => ({
    token: localStorage.getItem('iap_token') || '',
    role: 'user' as UserRole,
    permissions: userPermissions,
    userName: '普通演示用户',
    roleName: '普通用户',
  }),
  getters: {
    isAuthenticated: (state) => Boolean(state.token),
    isAdmin: (state) => state.role === 'admin',
  },
  actions: {
    hasPermission(permission: string) {
      return this.permissions.includes(permission);
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
    restoreSession() {
      const storedToken = localStorage.getItem('iap_token') || sessionStorage.getItem('iap_token') || '';
      if (!storedToken) return;

      this.token = storedToken;
      const storedRole = localStorage.getItem('iap_user_role') as UserRole | null;
      this.switchRole(storedRole === 'admin' ? 'admin' : 'user');
      this.userName = localStorage.getItem('iap_user_name') || this.userName;
    },
    logout() {
      this.token = '';
      localStorage.removeItem('iap_token');
      localStorage.removeItem('iap_user_role');
      localStorage.removeItem('iap_user_name');
      sessionStorage.removeItem('iap_token');
      this.switchRole('user');
    },
  },
});
