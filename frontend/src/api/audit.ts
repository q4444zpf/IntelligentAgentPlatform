import { request } from './client';

export type AuditCategory = 'runtime' | 'management';
export type AuditSource = 'agent' | 'tool' | 'mcp' | 'knowledge' | 'sandbox' | 'llm' | 'system';
export type AuditStatus = 'started' | 'succeeded' | 'failed' | 'cancelled';
export type AuditRisk = 'low' | 'medium' | 'high' | 'critical';
export type AuditActorRole = 'unknown' | 'user' | 'project_admin' | 'unit_auditor'
  | 'project_admin,user' | 'project_admin,unit_auditor' | 'unit_auditor,user'
  | 'project_admin,unit_auditor,user';

export interface AuditEventListItem {
  id: string;
  unit_id: string;
  project_id: string | null;
  user_id: string | null;
  actor_role: AuditActorRole;
  category: AuditCategory;
  source: AuditSource;
  action: string;
  status: AuditStatus;
  risk_level: AuditRisk;
  trace_id: string | null;
  run_id: string | null;
  resource_type: string | null;
  resource_id: string | null;
  resource_name: string | null;
  duration_ms: number | null;
  occurred_at: string;
}

export interface AuditEventDetail extends AuditEventListItem {
  parent_event_id: string | null;
  summary: string;
  metadata: Record<string, unknown>;
  error_code: string | null;
  created_at: string;
}

export interface AuditSummary {
  total: number;
  failed: number;
  high_risk: number;
  runtime: number;
  management: number;
  by_source: Partial<Record<AuditSource, number>>;
}

export interface AuditEventPage {
  items: AuditEventListItem[];
  page: number;
  page_size: number;
  total: number;
  summary: AuditSummary;
}

export interface AuditFilters {
  page?: number;
  page_size?: number;
  category?: AuditCategory;
  source?: AuditSource;
  action?: string;
  status?: AuditStatus;
  risk_level?: AuditRisk;
  project_id?: string;
  user_id?: string;
  query?: string;
  occurred_after?: string;
  occurred_before?: string;
}

const filterOrder: readonly (keyof AuditFilters)[] = [
  'page', 'page_size', 'category', 'source', 'action', 'status', 'risk_level',
  'project_id', 'user_id', 'query', 'occurred_after', 'occurred_before',
];

function hasValue(value: unknown): boolean {
  return value !== undefined && value !== null && value !== '' &&
    (!Array.isArray(value) || value.length > 0);
}

function auditQuery(filters: AuditFilters): string {
  const params = new URLSearchParams();
  const values = filters as Record<string, unknown>;

  for (const key of filterOrder) {
    const value = values[key];
    if (hasValue(value)) params.set(key, String(value));
  }

  const query = params.toString();
  return query ? `?${query}` : '';
}

export const auditApi = {
  list: (filters: AuditFilters = {}, signal?: AbortSignal) =>
    request<AuditEventPage>(`/audit/events${auditQuery(filters)}`, { signal }),
  get: (eventId: string, signal?: AbortSignal) =>
    request<AuditEventDetail>(`/audit/events/${encodeURIComponent(eventId)}`, { signal }),
  related: (eventId: string, signal?: AbortSignal) =>
    request<AuditEventListItem[]>(
      `/audit/events/${encodeURIComponent(eventId)}/related`,
      { signal },
    ),
};
