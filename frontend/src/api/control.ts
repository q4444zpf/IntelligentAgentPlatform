import { request } from './client';

export interface ControlPolicy { id: string; unit_id: string; project_id: string; asset_type: string; command_type: string; approval_mode: 'single' | 'dual'; enabled: boolean; }
export interface ControlCommand { id: string; policy_id: string; requester_id: string; target: Record<string, unknown>; parameters: Record<string, unknown>; simulation_artifact_id: string; valid_from: string; valid_until: string; required_approvals: number; status: string; expected_state?: Record<string, unknown> | null; actual_state?: Record<string, unknown> | null; deviation_result?: Record<string, unknown> | null; }
const id = (value: string) => encodeURIComponent(value);
export const controlApi = {
  listPolicies(signal?: AbortSignal) { return request<ControlPolicy[]>('/control/policies', { signal }); },
  listCommands(signal?: AbortSignal) { return request<ControlCommand[]>('/control/commands', { signal }); },
  getCommand(commandId: string) { return request<ControlCommand>(`/control/commands/${id(commandId)}`); },
  approve(commandId: string, reason?: string) { return request<ControlCommand>(`/control/commands/${id(commandId)}/approve`, { method: 'POST', body: JSON.stringify({ reason: reason?.trim() || null }) }); },
  reject(commandId: string, reason?: string) { return request<ControlCommand>(`/control/commands/${id(commandId)}/reject`, { method: 'POST', body: JSON.stringify({ reason: reason?.trim() || null }) }); },
  execute(commandId: string, key: string) { return request<ControlCommand>(`/control/commands/${id(commandId)}/execute`, { method: 'POST', headers: { 'Idempotency-Key': key } }); },
  feedback(commandId: string, actualState: Record<string, unknown>) { return request<ControlCommand>(`/control/commands/${id(commandId)}/feedback`, { method: 'POST', body: JSON.stringify({ actual_state: actualState }) }); },
  rollback(commandId: string, key: string) { return request<ControlCommand>(`/control/commands/${id(commandId)}/rollback`, { method: 'POST', headers: { 'Idempotency-Key': key } }); },
};
