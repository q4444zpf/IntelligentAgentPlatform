// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, request } from './client';
import { controlApi } from './control';

describe('control API contracts', () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it('decodes structured control error codes while preserving legacy detail errors', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce({ ok: false, status: 409, json: async () => ({ code: 'control_invalid_state', message: 'Invalid state', details: { status: 'expired' } }) })
      .mockResolvedValueOnce({ ok: false, status: 422, json: async () => ({ detail: 'Legacy validation' }) }));
    await expect(request('/control/commands')).rejects.toMatchObject({ message: 'Invalid state', status: 409, code: 'control_invalid_state', details: { status: 'expired' } });
    await expect(request('/control/commands')).rejects.toMatchObject({ message: 'Legacy validation', status: 422 });
    expect(new ApiError('bad', 400, 'code', { field: 'bad' }).code).toBe('code');
  });

  it('lists and filters policies and commands with encoded query values', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => [] });
    vi.stubGlobal('fetch', fetchMock);
    await controlApi.listPolicies({ enabled: false, asset_type: 'gate / east', command_type: 'open & close' });
    await controlApi.listCommands({ status: 'awaiting_feedback', policy_id: 'policy/1', created_after: '2026-09-15T12:00:00Z' });
    expect(fetchMock.mock.calls[0][0]).toBe('/api/control/policies?enabled=false&asset_type=gate+%2F+east&command_type=open+%26+close');
    expect(fetchMock.mock.calls[1][0]).toBe('/api/control/commands?status=awaiting_feedback&policy_id=policy%2F1&created_after=2026-09-15T12%3A00%3A00Z');
  });

  it('sends policy, proposal, feedback and action payloads to encoded resources', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal('fetch', fetchMock);
    await controlApi.getPolicy('policy/1');
    await controlApi.createPolicy({ asset_type: 'gate', command_type: 'open', parameter_schema: {}, max_command_lifetime_seconds: 600, approval_mode: 'dual', deviation_thresholds: {}, enabled: true });
    await controlApi.updatePolicy('policy/1', { enabled: false });
    await controlApi.propose({ policy_id: 'policy/1', target: { gate: '1' }, parameters: { opening: 20 }, simulation_artifact_id: 'artifact', valid_from: '2026-09-15T12:00:00Z', valid_until: '2026-09-15T12:10:00Z', rollback_plan: {}, expected_state: { opening: 20 }, idempotency_key: 'proposal-key' });
    await controlApi.feedback('gate/1', { actual_state: { opening: 18 }, observed_at: '2026-09-15T12:02:00Z' });
    await controlApi.execute('gate/1', 'execute-key');
    await controlApi.rollback('gate/1', 'rollback-key');
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(['/api/control/policies/policy%2F1', '/api/control/policies', '/api/control/policies/policy%2F1', '/api/control/commands', '/api/control/commands/gate%2F1/feedback', '/api/control/commands/gate%2F1/execute', '/api/control/commands/gate%2F1/rollback']);
    expect(fetchMock.mock.calls[3][1].body).toContain('"idempotency_key":"proposal-key"');
    expect(fetchMock.mock.calls[4][1].body).toBe('{"actual_state":{"opening":18},"observed_at":"2026-09-15T12:02:00Z"}');
    expect(fetchMock.mock.calls[5][1].headers.get('Idempotency-Key')).toBe('execute-key');
    expect(fetchMock.mock.calls[6][1].headers.get('Idempotency-Key')).toBe('rollback-key');
  });
});
