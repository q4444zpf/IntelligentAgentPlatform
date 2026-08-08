import { request } from './client';

export interface AuthPermission {
  code: string;
  target: 'unit' | 'current_project';
}

export interface AuthProject {
  id: string;
  name: string;
}

export interface AuthMenu {
  id: string;
  node_key: string;
  kind: 'group' | 'route';
  route_key: string | null;
  parent_id: string | null;
  title: string;
  sort_order: number;
}

export interface AuthContext {
  user: {
    id: string;
    display_name: string;
  };
  unit_id: string;
  current_project_id: string | null;
  current_project: AuthProject | null;
  projects: AuthProject[];
  auth_method: 'local' | 'oidc' | 'dev_test';
  authorization_version: number;
  roles: string[];
  permissions: AuthPermission[];
  menus: AuthMenu[];
  csrf_token: string;
  session: {
    idle_expires_at: string;
    absolute_expires_at: string;
  };
}

export interface AuthSessionSummary {
  session_id: string;
  auth_method: 'local' | 'oidc' | 'dev_test' | string;
  created_at: string;
  last_seen_at: string;
  current_project: { id: string; name: string } | null;
  is_current_session: boolean;
}

export interface AuthSessionListResponse {
  sessions: AuthSessionSummary[];
}

export interface LocalLoginPayload {
  email: string;
  password: string;
}

export interface LocalLoginResponse {
  status: string;
  auth_method: string;
  must_change_password: boolean;
}

export const authApi = {
  login: () => request<{ authorization_url: string }>('/auth/login'),
  localLogin: (payload: LocalLoginPayload) => request<LocalLoginResponse>('/auth/local/login', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  devLogin: () => request<{ status: string; auth_method: string }>('/auth/dev/login', { method: 'POST' }),
  me: () => request<AuthContext>('/auth/me'),
  logout: () => request<{ status: string }>('/auth/logout', { method: 'POST' }),
};

export const sessionApi = {
  list: (signal?: AbortSignal) => request<AuthSessionListResponse>('/auth/sessions', { signal }),
  revoke: (sessionId: string) => request<{ status: string; revoked: number }>(`/auth/sessions/${encodeURIComponent(sessionId)}/revoke`, { method: 'POST' }),
  revokeOthers: () => request<{ status: string; revoked: number }>('/auth/sessions/revoke-others', { method: 'POST' }),
};
