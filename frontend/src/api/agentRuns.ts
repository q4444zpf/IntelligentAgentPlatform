import { request } from './client';
import type { AgentRunInfo } from './conversations';
import { getRunEvents, type RunEvent } from './runEvents';
import type { ToolInvocationInfo } from './tools';

export type { RunEvent } from './runEvents';

export interface AgentRunFilters {
  page: number;
  page_size: number;
  status?: string;
  actor_id?: string;
  query?: string;
  started_after?: string;
  started_before?: string;
}

export interface AgentRunListItem extends AgentRunInfo {
  conversation_title: string;
  trigger_summary: string;
  tool_invocation_count: number;
  duration_ms: number;
}

export interface AgentRunSummary {
  total: number;
  completed: number;
  running: number;
  failed: number;
  tool_invocations: number;
}

export interface AgentRunPage {
  items: AgentRunListItem[];
  page: number;
  page_size: number;
  total: number;
  summary: AgentRunSummary;
}

export const agentRunsApi = {
  list: (filters: AgentRunFilters, signal?: AbortSignal) => {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== undefined && value !== '') params.set(key, String(value));
    });
    return request<AgentRunPage>(`/agent-runs?${params.toString()}`, { signal });
  },
  get: (runId: string, signal?: AbortSignal) =>
    request<AgentRunInfo>(`/agent-runs/${encodeURIComponent(runId)}`, { signal }),
  listInvocations: (runId: string, signal?: AbortSignal) =>
    request<ToolInvocationInfo[]>(
      `/agent-runs/${encodeURIComponent(runId)}/tool-invocations`,
      { signal },
    ),
  listEvents: (runId: string, signal?: AbortSignal): Promise<RunEvent[]> =>
    getRunEvents(runId, 0, signal),
};
