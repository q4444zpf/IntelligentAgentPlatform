import { beforeEach, describe, expect, it, vi } from 'vitest';

import { request } from './client';
import { approvalsApi } from './approvals';

vi.mock('./client', () => ({ request: vi.fn() }));

describe('approvalsApi', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists pending approvals with an abort signal', async () => {
    vi.mocked(request).mockResolvedValue([]);
    const controller = new AbortController();
    await approvalsApi.list('pending', controller.signal);
    expect(request).toHaveBeenCalledWith('/approvals?status=pending', { signal: controller.signal });
  });

  it('encodes approval ids and posts decisions with a reason', async () => {
    vi.mocked(request).mockResolvedValue({});
    await approvalsApi.approve('approval/1', '同意');
    expect(request).toHaveBeenCalledWith('/approvals/approval%2F1/approve', {
      method: 'POST',
      body: JSON.stringify({ reason: '同意' }),
    });
    await approvalsApi.reject('approval/1', '拒绝');
    expect(request).toHaveBeenLastCalledWith('/approvals/approval%2F1/reject', {
      method: 'POST',
      body: JSON.stringify({ reason: '拒绝' }),
    });
  });
});
