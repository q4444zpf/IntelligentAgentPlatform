import { request } from './client';

export type TeamFailureStrategy = 'fail_fast' | 'continue_then_synthesize';

export interface TeamMemberDraft {
  agent_id: string;
  responsibility: string;
  agent_definition_digest?: string | null;
  agent_definition?: Record<string, unknown> | null;
  tool_ids: string[];
  skill_names: string[];
  knowledge_source_ids: string[];
}

export interface TeamDraft {
  supervisor: TeamMemberDraft;
  members: TeamMemberDraft[];
  tool_ids: string[];
  skill_names: string[];
  knowledge_source_ids: string[];
  max_steps: number;
  max_parallel_members: number;
  timeout_seconds: number;
  failure_strategy: TeamFailureStrategy;
  approval_policy_id: string | null;
}

export interface TeamMemberInfo {
  agent_id: string;
  role: 'supervisor' | 'member';
  responsibility: string;
  agent_definition_digest: string | null;
}

export interface TeamSummary {
  id: string;
  unit_id: string;
  project_id: string;
  name: string;
  description: string;
  enabled: boolean;
  draft_revision: number;
  published_version: number | null;
  supervisor: TeamMemberInfo | null;
  member_count: number;
  updated_at: string;
}

export interface TeamVersionInfo {
  id: string;
  team_id: string;
  version: number;
  status: 'draft' | 'published';
  definition: TeamDraft;
  definition_digest: string | null;
  members: TeamMemberInfo[];
  max_steps: number;
  max_parallel_members: number;
  timeout_seconds: number;
  failure_strategy: TeamFailureStrategy;
  published_by: string | null;
  published_at: string | null;
}

export interface TeamListFilters { enabled?: boolean; published?: boolean }

const body = (value: unknown): RequestInit => ({ body: JSON.stringify(value) });
const teamPath = (id: string) => `/collaboration/teams/${encodeURIComponent(id)}`;

export const teamsApi = {
  list(filters: TeamListFilters = {}, signal?: AbortSignal) {
    const query = new URLSearchParams();
    if (filters.enabled !== undefined) query.set('enabled', String(filters.enabled));
    if (filters.published !== undefined) query.set('published', String(filters.published));
    const suffix = query.size ? `?${query}` : '';
    return request<TeamSummary[]>(`/collaboration/teams${suffix}`, { signal });
  },
  get: (id: string, signal?: AbortSignal) => request<TeamSummary>(teamPath(id), { signal }),
  create: (value: { id?: string; name: string; description: string }) => request<TeamSummary>('/collaboration/teams', { method: 'POST', ...body(value) }),
  update: (id: string, value: { name: string; description: string }) => request<TeamSummary>(teamPath(id), { method: 'PATCH', ...body(value) }),
  saveDraft: (id: string, revision: number, draft: TeamDraft) => request<TeamSummary>(`${teamPath(id)}/draft`, { method: 'PUT', ...body({ revision, draft }) }),
  publish: (id: string) => request<TeamVersionInfo>(`${teamPath(id)}/publish`, { method: 'POST' }),
  setEnabled: (id: string, enabled: boolean) => request<TeamSummary>(`${teamPath(id)}/${enabled ? 'enable' : 'disable'}`, { method: 'POST' }),
  listVersions: (id: string, signal?: AbortSignal) => request<TeamVersionInfo[]>(`${teamPath(id)}/versions`, { signal }),
  getVersion: (id: string, version: number, signal?: AbortSignal) => request<TeamVersionInfo>(`${teamPath(id)}/versions/${version}`, { signal }),
};
