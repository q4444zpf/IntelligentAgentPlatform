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

  it('omits actor_id when the backend should resolve the default agent', async () => {
    vi.mocked(conversationsApi.sendMessage).mockResolvedValue(structuredClone(acceptedRun));
    vi.mocked(getRunEvents).mockResolvedValue([
      { sequence: 2, event_type: 'run.status', payload: { status: 'completed' } },
    ]);
    vi.mocked(conversationsApi.listMessages).mockResolvedValue([acceptedRun.message]);
    const store = useConversationStore();
    store.activeConversationId = 'c1';

    await store.sendMessage('分析洪峰', 'agent');

    expect(conversationsApi.sendMessage).toHaveBeenCalledWith('c1', {
      content: '分析洪峰',
      actor_type: 'agent',
    });
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

  it('ignores an in-flight event response after switching conversations', async () => {
    let resolveEvents!: (events: Awaited<ReturnType<typeof getRunEvents>>) => void;
    vi.mocked(getRunEvents).mockReturnValue(new Promise((resolve) => {
      resolveEvents = resolve;
    }));
    vi.mocked(conversationsApi.listMessages).mockResolvedValue([
      { id: 'new-message', conversation_id: 'c2', role: 'user', content: '新会话', created_at: '2026-07-31T00:00:02Z' },
    ]);
    const store = useConversationStore();
    store.activeConversationId = 'c1';
    store.activeRun = structuredClone(acceptedRun.run);

    const replaying = store.replayEvents(0, 1);
    await Promise.resolve();
    await store.selectConversation('c2');
    resolveEvents([
      { sequence: 2, event_type: 'run.status', payload: { status: 'completed' } },
    ]);
    await replaying;

    expect(store.activeConversationId).toBe('c2');
    expect(store.activeRun).toBeNull();
    expect(store.events).toEqual([]);
    expect(store.messages).toHaveLength(1);
    expect(store.messages[0].conversation_id).toBe('c2');
  });

  it('merges out-of-order duplicate tool events by invocation and first sequence', async () => {
    vi.mocked(getRunEvents).mockResolvedValue([
      { sequence: 8, event_type: 'tool.completed', payload: { invocation_id: 'i1', tool_id: 'system.time', display_name: '当前时间', duration_ms: 12, result_summary: 'SECRET_RESULT' } },
      { sequence: 4, event_type: 'tool.started', payload: { invocation_id: 'i2', tool_id: 'system.context', display_name: '运行上下文', arguments_summary: 'SECRET_ARGUMENT' } },
      { sequence: 3, event_type: 'tool.started', payload: { invocation_id: 'i1', tool_id: 'system.time', display_name: '当前时间' } },
      { sequence: 9, event_type: 'tool.failed', payload: { invocation_id: 'i2', tool_id: 'system.context', display_name: '运行上下文', duration_ms: 7, secret: 'SECRET_TOKEN' } },
      { sequence: 9, event_type: 'tool.failed', payload: { invocation_id: 'i2', tool_id: 'system.context', display_name: '运行上下文', duration_ms: 7 } },
      { sequence: 10, event_type: 'run.status', payload: { status: 'failed' } },
    ]);
    const store = useConversationStore();
    store.activeConversationId = 'c1';
    store.activeRun = structuredClone(acceptedRun.run);

    await store.replayEvents(0, 1);

    expect(store.toolActivities).toEqual([
      { invocation_id: 'i1', display_name: '当前时间', tool_id: 'system.time', status: 'completed', duration_ms: 12, sequence: 3 },
      { invocation_id: 'i2', display_name: '运行上下文', tool_id: 'system.context', status: 'failed', duration_ms: 7, sequence: 4 },
    ]);
    expect(JSON.stringify(store.toolActivities)).not.toContain('SECRET_');
    expect(JSON.stringify(store.events)).not.toContain('SECRET_');
    expect(store.events.map((event) => event.sequence)).toEqual([3, 4, 8, 9, 10]);
  });

  it('keeps terminal tool status sticky across polling rounds and accepts a later terminal', async () => {
    vi.mocked(getRunEvents)
      .mockResolvedValueOnce([{ sequence: 2, event_type: 'tool.completed', payload: { invocation_id: 'i1', tool_id: 'system.time', display_name: '当前时间', duration_ms: 5 } }])
      .mockResolvedValueOnce([{ sequence: 3, event_type: 'tool.started', payload: { invocation_id: 'i1', tool_id: 'system.time', display_name: '当前时间' } }])
      .mockResolvedValueOnce([{ sequence: 4, event_type: 'tool.failed', payload: { invocation_id: 'i1', tool_id: 'system.time', display_name: '当前时间', duration_ms: 8 } }]);
    const store = useConversationStore(); store.activeConversationId = 'c1'; store.activeRun = structuredClone(acceptedRun.run);

    await store.replayEvents(0, 1);
    expect(store.toolActivities[0].status).toBe('completed');
    await store.replayEvents(0, 1);
    expect(store.toolActivities[0].status).toBe('completed');
    await store.replayEvents(0, 1);
    expect(store.toolActivities[0]).toEqual({ invocation_id: 'i1', display_name: '当前时间', tool_id: 'system.time', status: 'failed', duration_ms: 8, sequence: 2 });
  });
  it('clears tool activity when changing or resetting conversations', async () => {
    vi.mocked(conversationsApi.listMessages).mockResolvedValue([]);
    const store = useConversationStore();
    store.toolActivities = [{ invocation_id: 'i1', display_name: '当前时间', tool_id: 'system.time', status: 'running', duration_ms: null, sequence: 1 }];

    await store.selectConversation('c2');
    expect(store.toolActivities).toEqual([]);
    store.toolActivities.push({ invocation_id: 'i2', display_name: '上下文', tool_id: 'system.context', status: 'running', duration_ms: null, sequence: 2 });
    store.startNewConversation();
    expect(store.toolActivities).toEqual([]);
  });
});