import { request } from './client';

export interface IdentityUser {
  id: string;
  display_name: string;
  email: string | null;
  status: string;
  membership_status: string;
  project_memberships: IdentityProjectMembership[];
  role_summaries: IdentityRoleSummary[];
  /** Populated when the identity service exposes the user's authentication source. */
  auth_method?: 'local' | 'oidc' | 'dev_test' | string | null;
  external_identity?: boolean;
}

export interface IdentityProjectMembership {
  project_id: string;
  project_code: string;
  project_name: string;
  status: string;
}

export interface IdentityRoleSummary {
  role_id: string;
  code: string;
  name: string;
  scope_type: string;
  project_id: string | null;
}

export interface IdentityRole {
  id: string;
  code: string;
  name: string;
  scope_type: string;
  unit_id: string | null;
  built_in: boolean;
  status: string;
}

export interface IdentityPermission {
  id: string;
  code: string;
  resource: string;
  action: string;
  risk_level: string;
  status: string;
}

export function listIdentityUsers(signal?: AbortSignal): Promise<IdentityUser[]> {
  return request<IdentityUser[]>('/identity/users', { signal });
}

export function listIdentityRoles(signal?: AbortSignal): Promise<IdentityRole[]> {
  return request<IdentityRole[]>('/identity/roles', { signal });
}

export function listIdentityPermissions(signal?: AbortSignal): Promise<IdentityPermission[]> {
  return request<IdentityPermission[]>('/identity/permissions', { signal });
}

export function createIdentityUser(body: { display_name: string; email?: string | null; project_id?: string | null }): Promise<IdentityUser> {
  return request<IdentityUser>('/identity/users', { method: 'POST', body: JSON.stringify(body) });
}
export function updateIdentityUser(userId: string, body: { display_name: string; email?: string | null }): Promise<IdentityUser> {
  return request<IdentityUser>(`/identity/users/${encodeURIComponent(userId)}`, { method: 'PATCH', body: JSON.stringify(body) });
}

export function setIdentityUserStatus(userId: string, status: 'active' | 'inactive'): Promise<IdentityUser> {
  return request<IdentityUser>(`/identity/users/${encodeURIComponent(userId)}/status`, { method: 'POST', body: JSON.stringify({ status }) });
}

export function resetIdentityUserPassword(userId: string, body: { new_password: string }): Promise<{ user_id: string; must_change_password: boolean }> {
  return request<{ user_id: string; must_change_password: boolean }>(`/identity/users/${encodeURIComponent(userId)}/password-reset`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}
export function generateIdentityUserPassword(userId: string): Promise<{ user_id: string; must_change_password: boolean; generated_password: string }> {
  return request(`/identity/users/${encodeURIComponent(userId)}/password-generate`, { method: 'POST' });
}
export function deleteIdentityUser(userId: string): Promise<{ user_id: string; deleted: boolean }> {
  return request(`/identity/users/${encodeURIComponent(userId)}`, { method: 'DELETE' });
}

export function listIdentityUserRoles(userId: string, projectId?: string | null, signal?: AbortSignal): Promise<IdentityRoleSummary[]> {
  const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
  return request<IdentityRoleSummary[]>(`/identity/users/${encodeURIComponent(userId)}/roles${query}`, { signal });
}

export function assignIdentityUserRole(userId: string, body: { role_id: string; project_id?: string | null }): Promise<{ user_id: string; role_id: string; project_id: string | null }> {
  return request<{ user_id: string; role_id: string; project_id: string | null }>(`/identity/users/${encodeURIComponent(userId)}/roles`, { method: 'POST', body: JSON.stringify(body) });
}

export function removeIdentityUserRole(userId: string, body: { role_id: string; project_id?: string | null }): Promise<{ user_id: string; role_id: string; project_id: string | null; removed: boolean }> {
  return request<{ user_id: string; role_id: string; project_id: string | null; removed: boolean }>(`/identity/users/${encodeURIComponent(userId)}/roles`, { method: 'DELETE', body: JSON.stringify(body) });
}

export function replaceIdentityUserRoles(userId: string, body: { role_ids: string[]; project_id?: string | null }): Promise<IdentityRoleSummary[]> {
  return request<IdentityRoleSummary[]>(`/identity/users/${encodeURIComponent(userId)}/roles`, { method: 'PUT', body: JSON.stringify(body) });
}

export interface IdentityProject { id: string; unit_id: string; code: string; name: string; status: string }
export interface IdentityUnit { id: string; code: string; name: string; status: string }
export function listIdentityUnits(signal?: AbortSignal): Promise<IdentityUnit[]> { return request<IdentityUnit[]>('/identity/units', { signal }); }
export function updateIdentityUnit(unitId: string, body: { name: string }): Promise<IdentityUnit> { return request<IdentityUnit>(`/identity/units/${encodeURIComponent(unitId)}`, { method: 'PATCH', body: JSON.stringify(body) }); }
export function listIdentityProjects(signal?: AbortSignal): Promise<IdentityProject[]> { return request<IdentityProject[]>('/identity/projects', { signal }); }
export function createIdentityProject(body: { code: string; name: string }): Promise<IdentityProject> { return request<IdentityProject>('/identity/projects', { method: 'POST', body: JSON.stringify(body) }); }
export function updateIdentityProject(projectId: string, body: { name: string }): Promise<IdentityProject> { return request<IdentityProject>(`/identity/projects/${encodeURIComponent(projectId)}`, { method: 'PATCH', body: JSON.stringify(body) }); }
export function setIdentityProjectStatus(projectId: string, status: 'active' | 'inactive'): Promise<IdentityProject> { return request<IdentityProject>(`/identity/projects/${encodeURIComponent(projectId)}/status`, { method: 'POST', body: JSON.stringify({ status }) }); }
export function createIdentityRole(body: { code: string; name: string; scope_type: 'unit' | 'project' }): Promise<IdentityRole> { return request<IdentityRole>('/identity/roles', { method: 'POST', body: JSON.stringify(body) }); }
export function updateIdentityRole(roleId: string, body: { name: string }): Promise<IdentityRole> { return request<IdentityRole>(`/identity/roles/${encodeURIComponent(roleId)}`, { method: 'PATCH', body: JSON.stringify(body) }); }
export function setIdentityRoleStatus(roleId: string, status: 'active' | 'inactive'): Promise<IdentityRole> { return request<IdentityRole>(`/identity/roles/${encodeURIComponent(roleId)}/status`, { method: 'POST', body: JSON.stringify({ status }) }); }
export function deleteIdentityRole(roleId: string): Promise<{ role_id: string; deleted: boolean }> { return request<{ role_id: string; deleted: boolean }>(`/identity/roles/${encodeURIComponent(roleId)}`, { method: 'DELETE' }); }
export function grantIdentityRolePermission(
  roleId: string,
  body: { permission_code: string; data_scope: 'unit' | 'assigned_projects' | 'project' | 'own' | 'custom_projects' },
): Promise<{ role_id: string; permission_code: string; data_scope: string }> {
  return request<{ role_id: string; permission_code: string; data_scope: string }>(
    `/identity/roles/${encodeURIComponent(roleId)}/permissions`,
    { method: 'POST', body: JSON.stringify(body) },
  );
}
