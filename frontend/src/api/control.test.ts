import { beforeEach, describe, expect, it, vi } from 'vitest';
import { request } from './client';
import { controlApi } from './control';
vi.mock('./client', () => ({ request: vi.fn() }));
describe('controlApi', () => {
  beforeEach(() => vi.clearAllMocks());
  it('sends encoded ids and idempotency headers', async () => {
    vi.mocked(request).mockResolvedValue({});
    await controlApi.execute('gate/1', 'key-1');
    expect(request).toHaveBeenCalledWith('/control/commands/gate%2F1/execute', { method: 'POST', headers: { 'Idempotency-Key': 'key-1' } });
    await controlApi.rollback('gate/1', 'key-2');
    expect(request).toHaveBeenLastCalledWith('/control/commands/gate%2F1/rollback', { method: 'POST', headers: { 'Idempotency-Key': 'key-2' } });
  });
});
