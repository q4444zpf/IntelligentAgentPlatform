import { beforeEach, describe, expect, it, vi } from 'vitest';

import { request } from './client';
import { getRunEvents } from './runEvents';
import { agentRunsApi } from './agentRuns';

vi.mock('./client', () => ({ request: vi.fn() }));
vi.mock('./runEvents', () => ({ getRunEvents: vi.fn() }));

describe('agentRunsApi', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('encodes list filters and omits undefined or empty strings', async () => {
    vi.mocked(request).mockResolvedValue({});

    await agentRunsApi.list({
      page: 2,
      page_size: 25,
      status: 'in progress',
      actor_id: '',
      query: '洪水 预报',
      started_after: undefined,
      started_before: '2026-08-03T10:30:00+08:00',
    });

    expect(request).toHaveBeenCalledWith(
      '/agent-runs?page=2&page_size=25&status=in+progress&query=%E6%B4%AA%E6%B0%B4+%E9%A2%84%E6%8A%A5&started_before=2026-08-03T10%3A30%3A00%2B08%3A00',
      { signal: undefined },
    );
  });

  it('passes an AbortSignal to the shared request', async () => {
    vi.mocked(request).mockResolvedValue({});
    const controller = new AbortController();

    await agentRunsApi.list({ page: 1, page_size: 20 }, controller.signal);

    expect(request).toHaveBeenCalledWith('/agent-runs?page=1&page_size=20', {
      signal: controller.signal,
    });
  });

  it('encodes a run id when getting a run', async () => {
    vi.mocked(request).mockResolvedValue({});

    await agentRunsApi.get('run/1 测试');

    expect(request).toHaveBeenCalledWith('/agent-runs/run%2F1%20%E6%B5%8B%E8%AF%95', {
      signal: undefined,
    });
  });

  it('encodes a run id when listing tool invocations', async () => {
    vi.mocked(request).mockResolvedValue([]);

    await agentRunsApi.listInvocations('run/1 测试');

    expect(request).toHaveBeenCalledWith(
      '/agent-runs/run%2F1%20%E6%B5%8B%E8%AF%95/tool-invocations',
      { signal: undefined },
    );
  });

  it('delegates event loading to the existing bounded SSE reader', async () => {
    const events = [{ sequence: 1, event_type: 'run.started', payload: {} }];
    vi.mocked(getRunEvents).mockResolvedValue(events);

    await expect(agentRunsApi.listEvents('run/1')).resolves.toBe(events);
    expect(getRunEvents).toHaveBeenCalledWith('run/1', 0);
  });
});
