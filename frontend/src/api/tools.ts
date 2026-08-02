import { request } from './client';

export type ToolSource = 'builtin' | 'mcp' | 'knowledge' | 'artifact' | 'sandbox';
export type ToolRisk = 'low' | 'medium' | 'high' | 'critical';

export interface ToolInfo {
  tool_id: string;
  version: string;
  name: string;
  description: string;
  source: ToolSource;
  risk_level: ToolRisk;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  requires_approval: boolean;
  published: boolean;
  enabled: boolean;
  is_builtin: boolean;
  created_at: string;
  updated_at: string;
}

export interface ToolInvocationInfo {
  id: string;
  run_id: string;
  tool_call_id: string;
  tool_id: string;
  tool_version: string;
  status: string;
  arguments_summary: Record<string, unknown>;
  result_summary: Record<string, unknown> | null;
  duration_ms: number | null;
  error_code: string | null;
  created_at: string;
  completed_at: string | null;
}

export const toolsApi = {
  list: (signal?: AbortSignal) => request<ToolInfo[]>('/tools', { signal }),
  get: (toolId: string) => request<ToolInfo>(`/tools/${encodeURIComponent(toolId)}`),
  toggle: (toolId: string) => request<ToolInfo>(`/tools/${encodeURIComponent(toolId)}/toggle`, { method: 'PATCH' }),
  listInvocations: (runId: string, signal?: AbortSignal) =>
    request<ToolInvocationInfo[]>(`/agent-runs/${encodeURIComponent(runId)}/tool-invocations`, { signal }),
};