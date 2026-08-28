// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils';
import { nextTick, reactive } from 'vue';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AgentConsoleView from './AgentConsoleView.vue';
import source from './AgentConsoleView.vue?raw';

const mocks = vi.hoisted(() => ({
  listArtifacts: vi.fn(),
  downloadArtifact: vi.fn(),
}));

const store = reactive({ conversations: [], activeConversationId: 'c1', messages: [] as Array<Record<string, unknown>>, activeRun: { id: 'r1', status: 'running' }, error: '', sending: false, toolActivities: [
  { invocation_id: 'i1', display_name: '获取当前时间', tool_id: 'system.time', status: 'running', duration_ms: null, sequence: 1 },
  { invocation_id: 'i2', display_name: '运行上下文', tool_id: 'system.context', status: 'completed', duration_ms: 12, sequence: 2 },
  { invocation_id: 'i3', display_name: '失败工具', tool_id: 'system.fail', status: 'failed', duration_ms: 7, sequence: 3 },
], secret: 'SECRET_SENTINEL', loadConversations: vi.fn(), selectConversation: vi.fn(), startNewConversation: vi.fn(), sendMessage: vi.fn() });
vi.mock('@/stores/conversations', () => ({ useConversationStore: () => store }));
vi.mock('@/api/agents', () => ({ agentsApi: { list: vi.fn().mockResolvedValue([]) } }));
vi.mock('@/api/artifacts', () => ({
  artifactsApi: {
    list: mocks.listArtifacts,
    download: mocks.downloadArtifact,
  },
}));
vi.mock('vue-router', () => ({ useRoute: () => ({ meta: {} }), useRouter: () => ({ push: vi.fn() }) }));

const stubs = { 'a-select': { template: '<div />' } };

const artifact = (id: string, filename: string, contentType: string, runId: string | null) => ({
  id,
  filename,
  content_type: contentType,
  run_id: runId,
  size_bytes: 1536,
  created_at: '2026-08-28T01:02:03Z',
  status: 'available',
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise; });
  return { promise, resolve };
}

beforeEach(() => {
  store.messages = [];
  mocks.listArtifacts.mockReset();
  mocks.downloadArtifact.mockReset();
  mocks.listArtifacts.mockResolvedValue([]);
});

describe('AgentConsoleView runtime interactions', () => {
  it('renders safe compact tool status and duration without unrelated store secrets', () => {
    const wrapper = mount(AgentConsoleView, { global: { stubs } });
    expect(wrapper.text()).toContain('获取当前时间'); expect(wrapper.text()).toContain('执行中');
    expect(wrapper.text()).toContain('运行上下文'); expect(wrapper.text()).toContain('完成'); expect(wrapper.text()).toContain('12 ms');
    expect(wrapper.text()).toContain('失败工具'); expect(wrapper.text()).toContain('失败'); expect(wrapper.text()).toContain('7 ms');
    expect(wrapper.text()).not.toContain('SECRET_SENTINEL'); wrapper.unmount();
  });

  it('renders user text literally and associates only normalized HTML artifacts with each assistant run', async () => {
    store.messages = [
      { id: 'm-user', conversation_id: 'c1', role: 'user', content: '<img src=x onerror=alert(1)>', run_id: null, created_at: '2026-08-28T01:00:00Z' },
      { id: 'm-run-1', conversation_id: 'c1', role: 'assistant', content: '**第一轮结论**', run_id: 'run-1', created_at: '2026-08-28T01:01:00Z' },
      { id: 'm-run-2', conversation_id: 'c1', role: 'assistant', content: '第二轮结论', run_id: 'run-2', created_at: '2026-08-28T01:02:00Z' },
    ];
    mocks.downloadArtifact.mockResolvedValue({ url: 'https://objects.example/signed' });
    mocks.listArtifacts.mockResolvedValue([
      artifact('html-type', 'report.bin', '  Text/HTML; charset=utf-8  ', 'run-1'),
      artifact('xhtml-type', 'summary.xhtml', 'APPLICATION/XHTML+XML', 'run-1'),
      artifact('html-suffix', 'HYDROGRAPH.HTM', 'application/octet-stream', 'run-1'),
      artifact('non-html', 'observations.csv', 'text/csv', 'run-1'),
      artifact('other-run', 'other-run.html', 'text/html', 'run-2'),
      artifact('unlinked', 'unlinked.html', 'text/html', null),
    ]);

    const wrapper = mount(AgentConsoleView, { global: { stubs } });
    await vi.waitFor(() => expect(wrapper.text()).toContain('report.bin'));

    const rows = wrapper.findAll('.message-row');
    expect(rows[0].text()).toContain('<img src=x onerror=alert(1)>');
    expect(rows[0].find('img').exists()).toBe(false);
    expect(rows[1].get('.message-bubble strong').text()).toBe('第一轮结论');
    expect(rows[1].text()).toContain('report.bin');
    expect(rows[1].text()).toContain('summary.xhtml');
    expect(rows[1].text()).toContain('HYDROGRAPH.HTM');
    expect(rows[1].text()).not.toContain('observations.csv');
    expect(rows[1].text()).not.toContain('other-run.html');
    expect(rows[2].text()).toContain('other-run.html');
    expect(rows[2].text()).not.toContain('report.bin');
    expect(wrapper.text()).not.toContain('unlinked.html');

    store.messages = store.messages.map((message) => ({ ...message }));
    await nextTick();

    const reloadedRows = wrapper.findAll('.message-row');
    expect(reloadedRows[1].text()).toContain('report.bin');
    expect(reloadedRows[2].text()).toContain('other-run.html');
    expect(mocks.listArtifacts).toHaveBeenCalledTimes(1);

    await reloadedRows[1].get('[aria-label="预览 report.bin"]').trigger('click');
    await flushPromises();
    expect(wrapper.get('.conversation-layout').classes()).toContain('preview-open');
    expect(wrapper.get('iframe').attributes('src')).toBe('https://objects.example/signed');
    wrapper.unmount();
  });

  it('aborts superseded artifact lists, ignores their late results, and aborts on unmount', async () => {
    const oldRequest = deferred<ReturnType<typeof artifact>[]>();
    const newRequest = deferred<ReturnType<typeof artifact>[]>();
    const signals: AbortSignal[] = [];
    mocks.listArtifacts.mockImplementation((signal: AbortSignal) => {
      signals.push(signal);
      return signals.length === 1 ? oldRequest.promise : newRequest.promise;
    });
    store.messages = [
      { id: 'm-run-1', conversation_id: 'c1', role: 'assistant', content: '旧运行', run_id: 'run-1', created_at: '2026-08-28T01:01:00Z' },
    ];
    const wrapper = mount(AgentConsoleView, { global: { stubs } });

    store.messages = [
      { id: 'm-run-2', conversation_id: 'c1', role: 'assistant', content: '新运行', run_id: 'run-2', created_at: '2026-08-28T01:02:00Z' },
    ];
    await nextTick();
    expect(signals[0].aborted).toBe(true);

    newRequest.resolve([artifact('new', 'new.html', 'text/html', 'run-2')]);
    await flushPromises();
    oldRequest.resolve([artifact('old', 'old.html', 'text/html', 'run-1')]);
    await flushPromises();
    expect(wrapper.text()).toContain('new.html');
    expect(wrapper.text()).not.toContain('old.html');

    wrapper.unmount();
    expect(signals[1].aborted).toBe(true);
  });
});

describe('AgentConsoleView runtime contract', () => {
  it('keeps persisted conversation runs and backend default resolution', () => {
    expect(source).toContain('class="conversation-page"'); expect(source).toContain('useConversationStore');
    expect(source).toContain('hasExplicitAgentSelection'); expect(source).toContain('selectedAgentId.value || undefined');
    expect(source).not.toContain('arguments_summary'); expect(source).not.toContain('result_summary');
    expect(source).not.toContain('setTimeout'); expect(source).not.toContain('initialMessages'); expect(source).not.toContain('沙箱已隔离');
    expect(source).toContain("const mode = ref<ChatMode>('single')"); expect(source).toContain('title="多智能体运行时开发中" disabled');
    expect(source).toContain("agent.enabled && (agent.runtime_form === 'web' || agent.runtime_form === 'common')");
  });
});
