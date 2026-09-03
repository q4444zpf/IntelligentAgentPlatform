export type TeamFailureStrategy = 'fail_fast' | 'continue_then_synthesize';

export interface TeamMemberDraft {
  agent_id: string;
  responsibility: string;
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

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

function positiveInteger(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0 ? value : fallback;
}

function normalizeMember(value: unknown): TeamMemberDraft {
  const member = record(value);
  return {
    agent_id: stringValue(member.agent_id),
    responsibility: stringValue(member.responsibility),
    tool_ids: stringArray(member.tool_ids),
    skill_names: stringArray(member.skill_names),
    knowledge_source_ids: stringArray(member.knowledge_source_ids),
  };
}

export function normalizeTeamDraft(value: unknown): TeamDraft {
  const draft = record(value);
  return {
    supervisor: normalizeMember(draft.supervisor),
    members: Array.isArray(draft.members) ? draft.members.map(normalizeMember) : [],
    tool_ids: stringArray(draft.tool_ids),
    skill_names: stringArray(draft.skill_names),
    knowledge_source_ids: stringArray(draft.knowledge_source_ids),
    max_steps: positiveInteger(draft.max_steps, 8),
    max_parallel_members: positiveInteger(draft.max_parallel_members, 1),
    timeout_seconds: positiveInteger(draft.timeout_seconds, 600),
    failure_strategy: draft.failure_strategy === 'continue_then_synthesize'
      ? 'continue_then_synthesize'
      : 'fail_fast',
    approval_policy_id: typeof draft.approval_policy_id === 'string' ? draft.approval_policy_id : null,
  };
}
