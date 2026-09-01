// @vitest-environment happy-dom

import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it } from 'vitest';

import type { AuthContext } from '@/api/auth';
import { usePermissionStore } from '@/stores/permission';

import { router } from './index';

const authenticatedWithoutPermissions: AuthContext = {
  user: { id: 'user-without-role', display_name: '未分配角色用户' },
  unit_id: 'unit-1',
  current_project_id: null,
  current_project: null,
  projects: [],
  auth_method: 'local',
  authorization_version: 1,
  roles: [],
  permissions: [],
  menus: [],
  csrf_token: 'csrf-token',
  session: {
    idle_expires_at: '2026-08-25T04:00:00Z',
    absolute_expires_at: '2026-08-25T12:00:00Z',
  },
};

describe('router authorization guard', () => {
  beforeEach(async () => {
    setActivePinia(createPinia());
    usePermissionStore().applyAuthContext(authenticatedWithoutPermissions);
    await router.replace('/403');
  });

  it('shows the forbidden page instead of redirecting an unauthorized route to itself', async () => {
    await router.push('/dashboard');

    expect(router.currentRoute.value.path).toBe('/403');
  });
});
