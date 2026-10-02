import { request } from './client';

export type ControlStatus = 'pending_approval' | 'approved' | 'rejected' | 'expired' | 'executing' | 'awaiting_feedback' | 'succeeded' | 'failed' | 'unknown' | 'deviation' | 'rollback_pending' | 'rolled_back';
export type JsonObject = Record<string, unknown>;
export interface ControlPolicyInput {
  asset_type: string;
  command_type: string;
  parameter_schema: JsonObject;
  max_command_lifetime_seconds: number;
  approval_mode: 'single' | 'dual';
  deviation_thresholds: JsonObject;
  enabled: boolean;
}
export interface ControlPolicy extends ControlPolicyInput {
  id: string; unit_id: string; project_id: string; owner_id: string; revision: number; created_at: string; updated_at: string;
}
export type ControlPolicyUpdate = Partial<Pick<ControlPolicyInput, 'parameter_schema' | 'max_command_lifetime_seconds' | 'approval_mode' | 'deviation_thresholds' | 'enabled'>>;
export interface ControlProposal {
  policy_id: string; target: JsonObject; parameters: JsonObject; simulation_artifact_id: string;
  valid_from: string; valid_until: string; rollback_plan: JsonObject; expected_state: JsonObject | null;
  idempotency_key: string; run_id?: string | null; team_version_id?: string | null;
}
export type ControlProposalDraft = Omit<ControlProposal, 'idempotency_key'>;
export interface ControlFeedback { actual_state: JsonObject; observed_at?: string | null }
export interface ControlCommand {
  id: string; unit_id: string; project_id: string; policy_id: string; requester_id: string;
  run_id: string | null; team_version_id: string | null; policy_digest: string;
  policy_snapshot: JsonObject; deviation_thresholds_snapshot: JsonObject; feedback_observed_at: string | null;
  target: JsonObject; parameters: JsonObject; parameter_digest: string;
  simulation_artifact_id: string; simulation_digest: string; valid_from: string; valid_until: string;
  required_approvals: number; rollback_plan: JsonObject; expected_state: JsonObject | null;
  actual_state: JsonObject | null; deviation_result: JsonObject | null; external_command_ref: string | null;
  idempotency_key: string; status: ControlStatus;
}
export interface ControlCommandListItem extends ControlCommand { approval_count: number }
export interface ControlCommandDetail extends ControlCommandListItem {
  approval_decisions: { approver_id: string; decision: 'approve' | 'reject'; reason: string | null; decided_at: string }[];
  attempts: { id: string; kind: 'execute' | 'rollback'; outcome: 'pending' | 'succeeded' | 'failed' | 'unknown'; external_id: string | null; created_at: string; completed_at: string | null }[];
  feedback_records: { id: string; recorder_id: string; observed_at: string; actual_state: JsonObject; deviation_result: JsonObject }[];
  simulation_attestation: JsonObject; rollback_provenance: JsonObject | null;
}
export interface PolicyFilters { enabled?: boolean; asset_type?: string; command_type?: string }
export interface CommandFilters { status?: ControlStatus; policy_id?: string; created_after?: string; created_before?: string }

const id = encodeURIComponent;
function query(filters: PolicyFilters | CommandFilters = {}) {
  const values = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) if (value !== undefined && value !== '') values.set(key, String(value));
  const suffix = values.toString();
  return suffix ? `?${suffix}` : '';
}
const json = (body: unknown): RequestInit => ({ body: JSON.stringify(body) });
export const controlApi = {
  listPolicies(filters: PolicyFilters = {}, signal?: AbortSignal) { return request<ControlPolicy[]>(`/control/policies${query(filters)}`, { signal }); },
  getPolicy(policyId: string) { return request<ControlPolicy>(`/control/policies/${id(policyId)}`); },
  createPolicy(body: ControlPolicyInput) { return request<ControlPolicy>('/control/policies', { method: 'POST', ...json(body) }); },
  updatePolicy(policyId: string, body: ControlPolicyUpdate) { return request<ControlPolicy>(`/control/policies/${id(policyId)}`, { method: 'PATCH', ...json(body) }); },
  listCommands(filters: CommandFilters = {}, signal?: AbortSignal) { return request<ControlCommandListItem[]>(`/control/commands${query(filters)}`, { signal }); },
  getCommand(commandId: string) { return request<ControlCommandDetail>(`/control/commands/${id(commandId)}`); },
  propose(body: ControlProposal) { return request<ControlCommand>('/control/commands', { method: 'POST', ...json(body) }); },
  approve(commandId: string, reason?: string) { return request<ControlCommand>(`/control/commands/${id(commandId)}/approve`, { method: 'POST', ...json({ reason: reason?.trim() || null }) }); },
  reject(commandId: string, reason?: string) { return request<ControlCommand>(`/control/commands/${id(commandId)}/reject`, { method: 'POST', ...json({ reason: reason?.trim() || null }) }); },
  execute(commandId: string, key: string) { return request<ControlCommand>(`/control/commands/${id(commandId)}/execute`, { method: 'POST', headers: { 'Idempotency-Key': key } }); },
  feedback(commandId: string, body: ControlFeedback) { return request<ControlCommand>(`/control/commands/${id(commandId)}/feedback`, { method: 'POST', ...json(body) }); },
  rollback(commandId: string, key: string) { return request<ControlCommand>(`/control/commands/${id(commandId)}/rollback`, { method: 'POST', headers: { 'Idempotency-Key': key } }); },
};
