import { request } from './client';

export interface IdentityUser {
  id: string;
  display_name: string;
  email: string | null;
  status: string;
  membership_status: string;
  project_memberships: IdentityProjectMembership[];
  role_summaries: IdentityRoleSummary[];
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

export function setIdentityUserStatus(userId: string, status: 'active' | 'inactive'): Promise<IdentityUser> {
  return request<IdentityUser>(`/identity/users/${encodeURIComponent(userId)}/status`, { method: 'POST', body: JSON.stringify({ status }) });
}

export interface IdentityProject { id: string; unit_id: string; code: string; name: string; status: string }
export function listIdentityProjects(signal?: AbortSignal): Promise<IdentityProject[]> { return request<IdentityProject[]>('/identity/projects', { signal }); }
export function createIdentityProject(body: { code: string; name: string }): Promise<IdentityProject> { return request<IdentityProject>('/identity/projects', { method: 'POST', body: JSON.stringify(body) }); }
export function setIdentityProjectStatus(projectId: string, status: 'active' | 'inactive'): Promise<IdentityProject> { return request<IdentityProject>(`/identity/projects/${encodeURIComponent(projectId)}/status`, { method: 'POST', body: JSON.stringify({ status }) }); }
export function createIdentityRole(body: { code: string; name: string; scope_type: 'unit' | 'project' }): Promise<IdentityRole> { return request<IdentityRole>('/identity/roles', { method: 'POST', body: JSON.stringify(body) }); }
export function setIdentityRoleStatus(roleId: string, status: 'active' | 'inactive'): Promise<IdentityRole> { return request<IdentityRole>(`/identity/roles/${encodeURIComponent(roleId)}/status`, { method: 'POST', body: JSON.stringify({ status }) }); }
