import { beforeEach, describe, expect, it, vi } from 'vitest';

import { request } from './client';
import {
  assignIdentityUserRole,
  deleteIdentityRole,
  grantIdentityRolePermission,
  listIdentityUserRoles,
  removeIdentityUserRole,
  replaceIdentityUserRoles,
  resetIdentityUserPassword,
} from './identity';

vi.mock('./client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./client')>();
  return { ...actual, request: vi.fn() };
});

describe('identity role API', () => {
  beforeEach(() => vi.clearAllMocks());

  it('queries unit or project roles with encoded identifiers', async () => {
    vi.mocked(request).mockResolvedValue([]);
    const controller = new AbortController();
    await listIdentityUserRoles('user/1', 'project 1', controller.signal);
    expect(request).toHaveBeenCalledWith('/identity/users/user%2F1/roles?project_id=project%201', { signal: controller.signal });
  });

  it('assigns, removes, and replaces role bindings using the role contract', async () => {
    vi.mocked(request).mockResolvedValue({});
    await assignIdentityUserRole('user-1', { role_id: 'role-1', project_id: 'project-1' });
    await removeIdentityUserRole('user-1', { role_id: 'role-1', project_id: null });
    await replaceIdentityUserRoles('user-1', { role_ids: ['role-2'] });
    expect(request).toHaveBeenNthCalledWith(1, '/identity/users/user-1/roles', { method: 'POST', body: JSON.stringify({ role_id: 'role-1', project_id: 'project-1' }) });
    expect(request).toHaveBeenNthCalledWith(2, '/identity/users/user-1/roles', { method: 'DELETE', body: JSON.stringify({ role_id: 'role-1', project_id: null }) });
    expect(request).toHaveBeenNthCalledWith(3, '/identity/users/user-1/roles', { method: 'PUT', body: JSON.stringify({ role_ids: ['role-2'] }) });
  });

  it('deletes roles with encoded ids', async () => {
    vi.mocked(request).mockResolvedValue({ role_id: 'role/1', deleted: true });
    await deleteIdentityRole('role/1');
    expect(request).toHaveBeenCalledWith('/identity/roles/role%2F1', { method: 'DELETE' });
  });

  it('grants a permission to an encoded role identifier', async () => {
    vi.mocked(request).mockResolvedValue({ role_id: 'role/1', permission_code: 'system:users.read', data_scope: 'unit' });
    await grantIdentityRolePermission('role/1', { permission_code: 'system:users.read', data_scope: 'unit' });
    expect(request).toHaveBeenCalledWith('/identity/roles/role%2F1/permissions', {
      method: 'POST',
      body: JSON.stringify({ permission_code: 'system:users.read', data_scope: 'unit' }),
    });
  });

  it('resets a user password with an encoded identifier', async () => {
    vi.mocked(request).mockResolvedValue({ user_id: 'user/1', must_change_password: true });
    await resetIdentityUserPassword('user/1', { new_password: 'NewPassword123!' });
    expect(request).toHaveBeenCalledWith('/identity/users/user%2F1/password-reset', {
      method: 'POST',
      body: JSON.stringify({ new_password: 'NewPassword123!' }),
    });
  });
});
