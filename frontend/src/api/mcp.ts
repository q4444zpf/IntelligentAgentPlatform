import { request } from './client';

export type McpTransport = 'stdio' | 'streamable_http' | 'sse';

export interface McpClientInput {
  name: string;
  description: string;
  transport: McpTransport;
  url: string;
  headers: Record<string, string>;
  command: string;
  args: string[];
  env: Record<string, string>;
  cwd: string;
  enabled: boolean;
}

export interface McpClient extends McpClientInput {
  key: string;
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

const json = (body: unknown): RequestInit => ({
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const mcpApi = {
  list: (signal?: AbortSignal) => request<McpClient[]>('/mcp', { signal }),
  create: (body: McpClientInput & { key: string }) => request<McpClient>('/mcp', { method: 'POST', ...json(body) }),
  update: (key: string, body: McpClientInput) => request<McpClient>(`/mcp/${encodeURIComponent(key)}`, { method: 'PUT', ...json(body) }),
  toggle: (key: string) => request<McpClient>(`/mcp/${encodeURIComponent(key)}/toggle`, { method: 'PATCH' }),
  remove: (key: string) => request<{ message: string }>(`/mcp/${encodeURIComponent(key)}`, { method: 'DELETE' }),
  tools: (key: string) => request<McpTool[]>(`/mcp/${encodeURIComponent(key)}/tools`),
  syncTools: (key: string) => request<McpTool[]>(`/mcp/${encodeURIComponent(key)}/tools/sync`, { method: 'POST' }, 20000),
  updateTools: (key: string, tools: string[] | null) => request<McpTool[]>(`/mcp/${encodeURIComponent(key)}/tools`, { method: 'PUT', ...json({ tools }) }),
};
