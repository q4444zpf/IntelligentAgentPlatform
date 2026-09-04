import { request } from './client';
import type { TeamDraft, TeamFailureStrategy, TeamMemberDraft } from './teamDraft';

export { normalizeTeamDraft } from './teamDraft';
export type { TeamDraft, TeamFailureStrategy, TeamMemberDraft } from './teamDraft';

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

function editableMember(member: TeamMemberDraft): TeamMemberDraft {
  return {
    agent_id: member.agent_id,
    responsibility: member.responsibility,
    tool_ids: [...member.tool_ids],
    skill_names: [...member.skill_names],
    knowledge_source_ids: [...member.knowledge_source_ids],
  };
}

function editableDraft(draft: TeamDraft): TeamDraft {
  return {
    supervisor: editableMember(draft.supervisor),
    members: draft.members.map(editableMember),
    tool_ids: [...draft.tool_ids],
    skill_names: [...draft.skill_names],
    knowledge_source_ids: [...draft.knowledge_source_ids],
    max_steps: draft.max_steps,
    max_parallel_members: draft.max_parallel_members,
    timeout_seconds: draft.timeout_seconds,
    failure_strategy: draft.failure_strategy,
    approval_policy_id: draft.approval_policy_id,
  };
}

export const teamsApi = {
  list(filters: TeamListFilters = {}, signal?: AbortSignal) {
    const query = new URLSearchParams();
    if (filters.enabled !== undefined) query.set('enabled', String(filters.enabled));
    if (filters.published !== undefined) query.set('published', String(filters.published));
    const suffix = query.size ? `?${query}` : '';
    return request<TeamSummary[]>(`/collaboration/teams${suffix}`, { signal });
  },
  get: (id: string, signal?: AbortSignal) => request<TeamSummary>(teamPath(id), { signal }),
  create: (value: { name: string; description: string }) => request<TeamSummary>('/collaboration/teams', { method: 'POST', ...body({ name: value.name, description: value.description }) }),
  update: (id: string, value: { name: string; description: string }) => request<TeamSummary>(teamPath(id), { method: 'PATCH', ...body(value) }),
  saveDraft: (id: string, revision: number, draft: TeamDraft) => request<TeamSummary>(`${teamPath(id)}/draft`, { method: 'PUT', ...body({ revision, draft: editableDraft(draft) }) }),
  publish: (id: string) => request<TeamVersionInfo>(`${teamPath(id)}/publish`, { method: 'POST' }),
  setEnabled: (id: string, enabled: boolean) => request<TeamSummary>(`${teamPath(id)}/${enabled ? 'enable' : 'disable'}`, { method: 'POST' }),
  listVersions: (id: string, signal?: AbortSignal) => request<TeamVersionInfo[]>(`${teamPath(id)}/versions`, { signal }),
  getVersion: (id: string, version: number, signal?: AbortSignal) => request<TeamVersionInfo>(`${teamPath(id)}/versions/${version}`, { signal }),
};
