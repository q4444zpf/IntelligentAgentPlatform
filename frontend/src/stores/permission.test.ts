// @vitest-environment happy-dom

import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { usePermissionStore } from './permission';
import { authApi, type AuthContext } from '@/api/auth';

describe('permission store auth context', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it('maps backend session capabilities to current route permissions', () => {
    const store = usePermissionStore();
    const context: AuthContext = {
      user: { id: 'user-1', display_name: '平台管理员' },
      unit_id: 'unit-1',
      current_project_id: 'project-1',
      current_project: { id: 'project-1', name: '示例项目' },
      projects: [{ id: 'project-1', name: '示例项目' }],
      auth_method: 'oidc',
      authorization_version: 2,
      roles: ['unit_admin'],
      permissions: [
        { code: 'identity.read', target: 'unit' },
        { code: 'agent.run', target: 'current_project' },
        { code: 'dashboard.read', target: 'unit' },
      ],
      menus: [],
      csrf_token: 'csrf-token',
      session: {
        idle_expires_at: '2026-08-06T10:00:00Z',
        absolute_expires_at: '2026-08-06T18:00:00Z',
      },
    };

    store.applyAuthContext(context);

    expect(store.isAuthenticated).toBe(true);
    expect(store.userName).toBe('平台管理员');
    expect(store.roleName).toBe('unit_admin');
    expect(store.hasPermission('system:users')).toBe(true);
    expect(store.hasPermission('chat:view')).toBe(true);
    expect(store.hasPermission('dashboard:view')).toBe(true);
    expect(store.csrfToken).toBe('csrf-token');
  });

  it('logs in with local credentials before restoring the server session', async () => {
    const store = usePermissionStore();
    const localLogin = vi.spyOn(authApi, 'localLogin').mockResolvedValue({
      status: 'ok',
      auth_method: 'local',
      must_change_password: false,
    });
    const restore = vi.spyOn(store, 'refreshSession').mockResolvedValue({
      user: { id: 'user-1', display_name: '本地用户' },
      unit_id: 'unit-1',
      current_project_id: null,
      current_project: null,
      projects: [],
      auth_method: 'local',
      authorization_version: 1,
      roles: ['viewer'],
      permissions: [],
      menus: [],
      csrf_token: 'csrf-local',
      session: { idle_expires_at: '2026-08-08T10:00:00Z', absolute_expires_at: '2026-08-08T18:00:00Z' },
    });

    await store.loginWithLocalCredentials('alice@example.test', 'Password123!');

    expect(localLogin).toHaveBeenCalledWith({ email: 'alice@example.test', password: 'Password123!' });
    expect(restore).toHaveBeenCalledTimes(1);
  });
});
