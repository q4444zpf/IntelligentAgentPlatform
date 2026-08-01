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

describe('conversation store', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.mocked(getRunEvents).mockResolvedValue([]);
  });

  it('keeps the accepted run queued instead of fabricating a reply', async () => {
    vi.mocked(conversationsApi.sendMessage).mockResolvedValue({
      message: { id: 'm1', conversation_id: 'c1', role: 'user', content: '分析洪峰', created_at: '2026-07-31T00:00:00Z' },
      run: { id: 'r1', conversation_id: 'c1', trigger_message_id: 'm1', actor_type: 'agent', actor_id: 'flood', status: 'queued', created_at: '2026-07-31T00:00:00Z', updated_at: '2026-07-31T00:00:00Z' },
    });
    const store = useConversationStore();
    store.activeConversationId = 'c1';
    await store.sendMessage('分析洪峰', 'agent', 'flood');
    expect(store.activeRun?.status).toBe('queued');
    expect(store.messages).toHaveLength(1);
  });
});
