import { request } from './client';

export interface Approval {
  id: string;
  run_id: string;
  invocation_id: string;
  tool_id: string;
  tool_version: string;
  unit_id: string;
  project_id: string;
  requester_id: string;
  requester_roles: string[];
  assignee_role: string;
  risk_level: string;
  arguments_summary: Record<string, unknown>;
  arguments_digest: string;
  status: 'pending' | 'approved' | 'rejected' | 'expired' | 'cancelled';
  reason: string | null;
  decided_by: string | null;
  decision_reason: string | null;
  expires_at: string;
  decided_at: string | null;
  created_at: string;
  updated_at: string;
}

type ApprovalStatus = Approval['status'] | 'all';

function decisionBody(reason?: string) {
  return JSON.stringify({ reason: reason?.trim() || null });
}

export const approvalsApi = {
  list(status: ApprovalStatus = 'pending', signal?: AbortSignal) {
    return request<Approval[]>(`/approvals?status=${encodeURIComponent(status)}`, { signal });
  },
  get(approvalId: string, signal?: AbortSignal) {
    return request<Approval>(`/approvals/${encodeURIComponent(approvalId)}`, { signal });
  },
  approve(approvalId: string, reason?: string) {
    return request<Approval>(`/approvals/${encodeURIComponent(approvalId)}/approve`, {
      method: 'POST',
      body: decisionBody(reason),
    });
  },
  reject(approvalId: string, reason?: string) {
    return request<Approval>(`/approvals/${encodeURIComponent(approvalId)}/reject`, {
      method: 'POST',
      body: decisionBody(reason),
    });
  },
};
