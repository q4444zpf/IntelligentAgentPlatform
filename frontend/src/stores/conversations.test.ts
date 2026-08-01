import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { conversationsApi } from '@/api/conversations';
import { getRunEvents } from '@/api/runEvents';
import { useConversationStore } from './conversations';

vi.mock('@/api/conversations', () => ({
  conversationsApi: {
    list: vi.fn(),
    listMessages: vi.fn(),
    create: vi.fn(),
    sendMessage: vi.fn(),
  },
}));
vi.mock('@/api/runEvents', () => ({ getRunEvents: vi.fn() }));

const acceptedRun = {
  message: { id: 'm1', conversation_id: 'c1', role: 'user' as const, content: '分析洪峰', created_at: '2026-07-31T00:00:00Z' },
  run: { id: 'r1', conversation_id: 'c1', trigger_message_id: 'm1', actor_type: 'agent' as const, actor_id: 'flood', status: 'queued', created_at: '2026-07-31T00:00:00Z', updated_at: '2026-07-31T00:00:00Z' },
};

describe('conversation store', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.resetAllMocks();
    vi.useRealTimers();
  });

  it('polls incrementally and reloads messages after completion', async () => {
    vi.useFakeTimers();
    vi.mocked(conversationsApi.sendMessage).mockResolvedValue(structuredClone(acceptedRun));
    vi.mocked(getRunEvents)
      .mockResolvedValueOnce([
        { sequence: 2, event_type: 'run.status', payload: { status: 'running' } },
      ])
      .mockResolvedValueOnce([
        { sequence: 3, event_type: 'message.completed', payload: { message_id: 'm2', role: 'assistant' } },
        { sequence: 4, event_type: 'run.status', payload: { status: 'completed' } },
      ]);
    vi.mocked(conversationsApi.listMessages).mockResolvedValue([
      acceptedRun.message,
      { id: 'm2', conversation_id: 'c1', role: 'assistant', content: '研判完成', created_at: '2026-07-31T00:00:01Z' },
    ]);
    const store = useConversationStore();
    store.activeConversationId = 'c1';

    const sending = store.sendMessage('分析洪峰', 'agent', 'flood');
    await vi.runAllTimersAsync();
    await sending;

    expect(getRunEvents).toHaveBeenNthCalledWith(1, 'r1', 0);
    expect(getRunEvents).toHaveBeenNthCalledWith(2, 'r1', 2);
    expect(store.activeRun?.status).toBe('completed');
    expect(store.messages.at(-1)?.content).toBe('研判完成');
    expect(conversationsApi.listMessages).toHaveBeenCalledWith('c1');
  });

  it('stops polling and exposes the safe runtime error after failure', async () => {
    vi.mocked(conversationsApi.sendMessage).mockResolvedValue(structuredClone(acceptedRun));
    vi.mocked(getRunEvents).mockResolvedValue([
      { sequence: 2, event_type: 'run.error', payload: { code: 'model_request_failed', message: '模型调用失败，请检查默认模型配置或稍后重试' } },
      { sequence: 3, event_type: 'run.status', payload: { status: 'failed' } },
    ]);
    const store = useConversationStore();
    store.activeConversationId = 'c1';

    await store.sendMessage('分析洪峰', 'agent', 'flood');

    expect(getRunEvents).toHaveBeenCalledTimes(1);
    expect(store.activeRun?.status).toBe('failed');
    expect(store.error).toBe('模型调用失败，请检查默认模型配置或稍后重试');
  });
});