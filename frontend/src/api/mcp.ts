import { request } from './client';

export type McpTransport = 'stdio' | 'streamable_http' | 'sse';

export interface McpClientInput {
  name: string;
  description: string;
  transport: McpTransport;
  url: string;
  headers: Record<string, string>;
  credential_id: string | null;
  command: string;
  args: string[];
  env: Record<string, string>;
  cwd: string;
  enabled: boolean;
}

export interface McpClient extends McpClientInput {
  key: string;
  client_id: string;
  status: 'active' | 'archived';
  health_status: 'not_checked' | 'healthy' | 'degraded' | 'offline';
  tools: string[] | null;
  tool_count: number;
  enabled_tool_count: number;
  last_synced_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface McpTool {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  enabled: boolean;
}

export interface McpOperation {
  id: string;
  client_id: string;
  operation_type: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  phase: string;
  result: { tool_count?: number } | null;
  error_code: string | null;
  error_message: string | null;
}

export interface McpHealth {
  health_status: McpClient['health_status'];
  last_checked_at: string | null;
  last_success_at: string | null;
  last_latency_ms: number | null;
  failure_count: number;
  last_error_code: string | null;
  last_error_message: string | null;
}

const json = (body: unknown): RequestInit => ({
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const mcpApi = {
  list: (includeArchived = false, signal?: AbortSignal) => request<McpClient[]>(`/mcp${includeArchived ? '?include_archived=true' : ''}`, { signal }),
  create: (body: McpClientInput & { key: string }) => request<McpClient>('/mcp', { method: 'POST', ...json(body) }),
  update: (key: string, body: McpClientInput) => request<McpClient>(`/mcp/${encodeURIComponent(key)}`, { method: 'PUT', ...json(body) }),
  toggle: (key: string) => request<McpClient>(`/mcp/${encodeURIComponent(key)}/toggle`, { method: 'PATCH' }),
  remove: (key: string) => request<{ message: string }>(`/mcp/${encodeURIComponent(key)}`, { method: 'DELETE' }),
  tools: (key: string) => request<McpTool[]>(`/mcp/${encodeURIComponent(key)}/tools`),
  syncTools: (key: string) => request<McpTool[]>(`/mcp/${encodeURIComponent(key)}/tools/sync`, { method: 'POST' }, 20000),
  updateTools: (key: string, tools: string[] | null) => request<McpTool[]>(`/mcp/${encodeURIComponent(key)}/tools`, { method: 'PUT', ...json({ tools }) }),
  testConnection: (key: string) => request<McpOperation>(`/mcp/${encodeURIComponent(key)}/test`, { method: 'POST' }, 20000),
  operation: (id: string) => request<McpOperation>(`/mcp/operations/${encodeURIComponent(id)}`),
  health: (key: string) => request<McpHealth>(`/mcp/${encodeURIComponent(key)}/health`),
  projects: (key: string) => request<{ project_ids: string[] }>(`/mcp/${encodeURIComponent(key)}/projects`),
  updateProjects: (key: string, projectIds: string[]) => request<{ project_ids: string[] }>(`/mcp/${encodeURIComponent(key)}/projects`, { method: 'PUT', ...json({ project_ids: projectIds }) }),
  archive: (key: string) => request<McpClient>(`/mcp/${encodeURIComponent(key)}/archive`, { method: 'POST' }),
  restore: (key: string) => request<McpClient>(`/mcp/${encodeURIComponent(key)}/restore`, { method: 'POST' }),
};
