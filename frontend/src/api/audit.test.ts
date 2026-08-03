import { beforeEach, describe, expect, it, vi } from 'vitest';

import { auditApi } from './audit';
import { ApiError, request } from './client';

vi.mock('./client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./client')>();
  return { ...actual, request: vi.fn() };
});

describe('auditApi', () => {
  beforeEach(() => vi.clearAllMocks());

  it('omits absent and empty filters without a trailing question mark', async () => {
    vi.mocked(request).mockResolvedValue({});
    await auditApi.list({ category: undefined, source: null, action: '', query: [] } as never);
    expect(request).toHaveBeenCalledWith('/audit/events', { signal: undefined });
  });

  it('serializes filters in stable contract order with exact scalar values', async () => {
    vi.mocked(request).mockResolvedValue({});
    await auditApi.list({
      occurred_before: '2026-08-04T12:00:00+08:00', query: '100%_\\',
      user_id: 'user-1', project_id: 'project-1', risk_level: 'critical',
      status: 'cancelled', action: 'agent.run', source: 'llm', category: 'runtime',
      page_size: 100, page: 2, occurred_after: '2026-08-04T10:00:00+08:00',
    });
    expect(request).toHaveBeenCalledWith(
      '/audit/events?page=2&page_size=100&category=runtime&source=llm&action=agent.run&status=cancelled&risk_level=critical&project_id=project-1&user_id=user-1&query=100%25_%5C&occurred_after=2026-08-04T10%3A00%3A00%2B08%3A00&occurred_before=2026-08-04T12%3A00%3A00%2B08%3A00',
      { signal: undefined },
    );
  });

  it('does not forward runtime keys outside the backend filter contract', async () => {
    vi.mocked(request).mockResolvedValue({});
    await auditApi.list({
      page: 1,
      page_size: 20,
      include_system: false,
      unknown_field: 'value',
    } as never);
    expect(request).toHaveBeenCalledWith('/audit/events?page=1&page_size=20', { signal: undefined });
  });

  it('encodes ids and forwards AbortSignal for all endpoints', async () => {
    vi.mocked(request).mockResolvedValue({});
    const controller = new AbortController();
    await auditApi.list({}, controller.signal);
    await auditApi.get('event/1 测试', controller.signal);
    await auditApi.related('event/1 测试', controller.signal);
    expect(request).toHaveBeenNthCalledWith(1, '/audit/events', { signal: controller.signal });
    expect(request).toHaveBeenNthCalledWith(2, '/audit/events/event%2F1%20%E6%B5%8B%E8%AF%95', { signal: controller.signal });
    expect(request).toHaveBeenNthCalledWith(3, '/audit/events/event%2F1%20%E6%B5%8B%E8%AF%95/related', { signal: controller.signal });
  });

  it('leaves shared request errors unchanged', async () => {
    const error = new ApiError('not found', 404);
    vi.mocked(request).mockRejectedValue(error);
    await expect(auditApi.get('missing')).rejects.toBe(error);
  });
});
