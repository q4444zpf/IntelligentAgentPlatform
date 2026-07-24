import { request } from './client';

export type AgentRuntimeForm = 'web' | 'desktop' | 'common';
export type AgentApprovalPolicy = 'never' | 'control_commands' | 'always';

export interface AgentInput {
  name: string;
  description: string;
  runtime_form: AgentRuntimeForm;
  language: 'zh-CN' | 'en-US';
  provider_id: string;
  model: string;
  system_prompt: string;
  context_prompt: string;
  approval_policy: AgentApprovalPolicy;
  skill_names: string[];
  enabled: boolean;
}

export interface AgentInfo extends AgentInput {
  id: string;
  pinned: boolean;
  startup_status: 'ready' | 'disabled';
  workspace_dir: string;
  created_at: string;
  updated_at: string;
}

const json = (body: unknown): RequestInit => ({
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const agentsApi = {
  list: (signal?: AbortSignal) => request<AgentInfo[]>('/agents', { signal }),
  create: (body: AgentInput & { id: string }) => request<AgentInfo>('/agents', { method: 'POST', ...json(body) }),
  update: (id: string, body: AgentInput) => request<AgentInfo>(`/agents/${encodeURIComponent(id)}`, { method: 'PUT', ...json(body) }),
  toggle: (id: string, enabled: boolean) => request<AgentInfo>(`/agents/${encodeURIComponent(id)}/toggle`, { method: 'PATCH', ...json({ enabled }) }),
  pin: (id: string, pinned: boolean) => request<AgentInfo>(`/agents/${encodeURIComponent(id)}/pin`, { method: 'PATCH', ...json({ pinned }) }),
  copy: (id: string, body: { id: string; name: string; copy_skills: boolean }) => request<AgentInfo>(`/agents/${encodeURIComponent(id)}/copy`, { method: 'POST', ...json(body) }),
  remove: (id: string) => request<{ success: boolean; agent_id: string }>(`/agents/${encodeURIComponent(id)}`, { method: 'DELETE' }),
};
