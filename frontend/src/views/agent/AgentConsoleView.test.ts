// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils';
import { nextTick, reactive } from 'vue';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AssistantMessageContent from '@/components/chat/AssistantMessageContent.vue';
import AgentConsoleView from './AgentConsoleView.vue';
import source from './AgentConsoleView.vue?raw';

const mocks = vi.hoisted(() => ({
  listArtifacts: vi.fn(),
  downloadArtifact: vi.fn(),
  previewArtifact: vi.fn(),
  listTeams: vi.fn(),
  getTeamVersion: vi.fn(),
}));

const store = reactive({ conversations: [] as Array<Record<string, unknown>>, activeConversationId: 'c1', messages: [] as Array<Record<string, unknown>>, activeRun: { id: 'r1', status: 'running' }, events: [] as Array<Record<string, unknown>>, error: '', sending: false, toolActivities: [
  { invocation_id: 'i1', display_name: '获取当前时间', tool_id: 'system.time', status: 'running', duration_ms: null, sequence: 1 },
  { invocation_id: 'i2', display_name: '运行上下文', tool_id: 'system.context', status: 'completed', duration_ms: 12, sequence: 2 },
  { invocation_id: 'i3', display_name: '失败工具', tool_id: 'system.fail', status: 'failed', duration_ms: 7, sequence: 3 },
], secret: 'SECRET_SENTINEL', loadConversations: vi.fn(), selectConversation: vi.fn(), startNewConversation: vi.fn(), sendMessage: vi.fn() });
vi.mock('@/stores/conversations', () => ({ useConversationStore: () => store }));
vi.mock('@/api/agents', () => ({ agentsApi: { list: vi.fn().mockResolvedValue([]) } }));
vi.mock('@/api/teams', () => ({ teamsApi: { list: mocks.listTeams, getVersion: mocks.getTeamVersion } }));
vi.mock('@/api/artifacts', () => ({
  artifactsApi: {
    list: mocks.listArtifacts,
    download: mocks.downloadArtifact,
    preview: mocks.previewArtifact,
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
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

function useMediumViewport() {
  vi.stubGlobal('matchMedia', vi.fn((query: string) => ({
    matches: query === '(min-width: 901px) and (max-width: 1250px)',
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(() => true),
  })));
}

beforeEach(() => {
  store.conversations = [];
  store.activeConversationId = 'c1';
  store.messages = [];
  store.events = [];
  store.selectConversation.mockClear();
  store.startNewConversation.mockClear();
  store.sendMessage.mockClear();
  mocks.listArtifacts.mockReset();
  mocks.downloadArtifact.mockReset();
  mocks.previewArtifact.mockReset();
  mocks.listArtifacts.mockResolvedValue([]);
  mocks.previewArtifact.mockResolvedValue({ url: 'https://objects.example/signed' });
  mocks.listTeams.mockReset(); mocks.getTeamVersion.mockReset();
  mocks.listTeams.mockResolvedValue([]);
});

afterEach(() => {
  vi.unstubAllGlobals();
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
      artifact('near-html', 'near-html.bin', 'text/htmlfoo', 'run-1'),
      artifact('near-xhtml', 'near-xhtml.bin', 'application/xhtml+xml-extra', 'run-1'),
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
    expect(rows[1].text()).not.toContain('near-html.bin');
    expect(rows[1].text()).not.toContain('near-xhtml.bin');
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

  it('keeps system and tool messages as literal text without rich rendering or Artifact cards', async () => {
    store.messages = [
      { id: 'm-user', conversation_id: 'c1', role: 'user', content: '<img src=x onerror=alert(1)>', run_id: null, created_at: '2026-08-28T01:00:00Z' },
      { id: 'm-assistant', conversation_id: 'c1', role: 'assistant', content: '**assistant rich**', run_id: 'run-assistant', created_at: '2026-08-28T01:01:00Z' },
      { id: 'm-system', conversation_id: 'c1', role: 'system', content: '<img src=x onerror=system()>\n```mermaid\nflowchart TD\nA-->B\n```', run_id: 'run-system', created_at: '2026-08-28T01:02:00Z' },
      { id: 'm-tool', conversation_id: 'c1', role: 'tool', content: '<img src=x onerror=tool()>\n```echarts\n{"series":[]}\n```', run_id: 'run-tool', created_at: '2026-08-28T01:03:00Z' },
    ];
    mocks.listArtifacts.mockResolvedValue([
      artifact('assistant-html', 'assistant.html', 'text/html', 'run-assistant'),
      artifact('system-html', 'system.html', 'text/html', 'run-system'),
      artifact('tool-html', 'tool.html', 'text/html', 'run-tool'),
    ]);

    const wrapper = mount(AgentConsoleView, { global: { stubs } });
    await vi.waitFor(() => expect(wrapper.text()).toContain('assistant.html'));
    const rows = wrapper.findAll('.message-row');

    expect(wrapper.findAllComponents(AssistantMessageContent)).toHaveLength(1);
    expect(rows[0].find('img').exists()).toBe(false);
    expect(rows[1].text()).toContain('assistant.html');
    expect(rows[2].text()).toContain('```mermaid');
    expect(rows[2].find('img').exists()).toBe(false);
    expect(rows[2].text()).not.toContain('system.html');
    expect(rows[3].text()).toContain('```echarts');
    expect(rows[3].find('img').exists()).toBe(false);
    expect(rows[3].text()).not.toContain('tool.html');
    wrapper.unmount();
  });

  it('closes the selected Artifact preview when switching to another conversation and can reopen it', async () => {
    store.conversations = [
      { id: 'c1', title: '当前会话', updated_at: '2026-08-28T01:00:00Z' },
      { id: 'c2', title: '另一会话', updated_at: '2026-08-28T02:00:00Z' },
    ];
    store.messages = [
      { id: 'm-run-1', conversation_id: 'c1', role: 'assistant', content: '结论', run_id: 'run-1', created_at: '2026-08-28T01:01:00Z' },
    ];
    mocks.listArtifacts.mockResolvedValue([artifact('html', 'report.html', 'text/html', 'run-1')]);
    mocks.downloadArtifact.mockResolvedValue({ url: 'https://objects.example/signed' });
    const wrapper = mount(AgentConsoleView, { global: { stubs } });
    await vi.waitFor(() => expect(wrapper.text()).toContain('report.html'));
    await wrapper.get('[aria-label="预览 report.html"]').trigger('click');
    await flushPromises();

    const otherConversation = wrapper.findAll('.history-list button').find((button) => button.text().includes('另一会话'))!;
    await otherConversation.trigger('click');
    expect(wrapper.find('iframe').exists()).toBe(false);
    expect(wrapper.get('.conversation-layout').classes()).not.toContain('preview-open');

    await wrapper.get('[aria-label="预览 report.html"]').trigger('click');
    await flushPromises();
    expect(wrapper.find('iframe').exists()).toBe(true);
    wrapper.unmount();
  });

  it('closes the selected Artifact preview for a new conversation and can reopen it', async () => {
    store.messages = [
      { id: 'm-run-1', conversation_id: 'c1', role: 'assistant', content: '结论', run_id: 'run-1', created_at: '2026-08-28T01:01:00Z' },
    ];
    mocks.listArtifacts.mockResolvedValue([artifact('html', 'report.html', 'text/html', 'run-1')]);
    mocks.downloadArtifact.mockResolvedValue({ url: 'https://objects.example/signed' });
    const wrapper = mount(AgentConsoleView, { global: { stubs } });
    await vi.waitFor(() => expect(wrapper.text()).toContain('report.html'));
    await wrapper.get('[aria-label="预览 report.html"]').trigger('click');
    await flushPromises();

    await wrapper.get('.new-chat-button').trigger('click');
    expect(wrapper.find('iframe').exists()).toBe(false);
    expect(wrapper.get('.conversation-layout').classes()).not.toContain('preview-open');

    await wrapper.get('[aria-label="预览 report.html"]').trigger('click');
    await flushPromises();
    expect(wrapper.find('iframe').exists()).toBe(true);
    wrapper.unmount();
  });

  it('keeps chat and composer visible while removing the context rail for a medium-width preview', async () => {
    useMediumViewport();
    store.messages = [
      { id: 'm-run-1', conversation_id: 'c1', role: 'assistant', content: '结论', run_id: 'run-1', created_at: '2026-08-28T01:01:00Z' },
    ];
    mocks.listArtifacts.mockResolvedValue([artifact('html', 'report.html', 'text/html', 'run-1')]);
    mocks.downloadArtifact.mockResolvedValue({ url: 'https://objects.example/signed' });
    const wrapper = mount(AgentConsoleView, { global: { stubs } });
    await vi.waitFor(() => expect(wrapper.text()).toContain('report.html'));

    await wrapper.get('[aria-label="预览 report.html"]').trigger('click');
    await flushPromises();
    expect(wrapper.find('.context-panel').exists()).toBe(false);
    expect(wrapper.find('.chat-workspace').exists()).toBe(true);
    expect(wrapper.find('.composer-shell').exists()).toBe(true);
    expect(wrapper.find('iframe').exists()).toBe(true);

    await wrapper.get('[aria-label="关闭 HTML 预览"]').trigger('click');
    expect(wrapper.find('.context-panel').exists()).toBe(true);
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

  it('ignores a non-abort rejection from a superseded artifact list', async () => {
    const staleRequest = deferred<ReturnType<typeof artifact>[]>();
    const currentRequest = deferred<ReturnType<typeof artifact>[]>();
    mocks.listArtifacts
      .mockImplementationOnce(() => staleRequest.promise)
      .mockImplementationOnce(() => currentRequest.promise);
    store.messages = [
      { id: 'm-run-1', conversation_id: 'c1', role: 'assistant', content: '旧运行', run_id: 'run-1', created_at: '2026-08-28T01:01:00Z' },
    ];
    const wrapper = mount(AgentConsoleView, { global: { stubs } });

    store.messages = [
      { id: 'm-run-2', conversation_id: 'c1', role: 'assistant', content: '新运行', run_id: 'run-2', created_at: '2026-08-28T01:02:00Z' },
    ];
    await nextTick();
    currentRequest.resolve([artifact('current', 'current.html', 'text/html', 'run-2')]);
    await flushPromises();
    expect(wrapper.text()).toContain('current.html');

    staleRequest.reject(new Error('stale transport failure'));
    await flushPromises();

    expect(wrapper.text()).toContain('current.html');
    wrapper.unmount();
  });
});

describe('AgentConsoleView runtime contract', () => {
  it('keeps persisted conversation runs and backend default resolution', () => {
    expect(source).toContain('class="conversation-page"'); expect(source).toContain('useConversationStore');
    expect(source).toContain('hasExplicitAgentSelection'); expect(source).toContain('selectedAgentId.value || undefined');
    expect(source).not.toContain('arguments_summary'); expect(source).not.toContain('result_summary');
    expect(source).not.toContain('setTimeout'); expect(source).not.toContain('initialMessages'); expect(source).not.toContain('沙箱已隔离');
    expect(source).toContain("const mode = ref<ChatMode>('single')"); expect(source).not.toContain('多智能体运行时开发中');
    expect(source).toContain("agent.enabled && (agent.runtime_form === 'web' || agent.runtime_form === 'common')");
  });
});

describe('AgentConsoleView Team runs', () => {
  const team = { id: 'team-1', name: '北江联合研判', description: '防洪会商', enabled: true, published_version: 2, supervisor: { agent_id: 'forecast', responsibility: '统筹研判' }, member_count: 1 };
  const version = { version: 2, definition: { supervisor: { agent_id: 'forecast', responsibility: '统筹研判' }, members: [{ agent_id: 'review', responsibility: '复核成果' }] } };

  it('selects an enabled published Team and sends its real actor id', async () => {
    mocks.listTeams.mockResolvedValue([team, { ...team, id: 'disabled', enabled: false }, { ...team, id: 'draft', published_version: null }]);
    mocks.getTeamVersion.mockResolvedValue(version);
    store.sendMessage.mockResolvedValue(undefined);
    const wrapper = mount(AgentConsoleView, { global: { stubs } }); await flushPromises();
    expect(mocks.listTeams).toHaveBeenCalledWith({ enabled: true, published: true });
    await wrapper.get('[data-testid="mode-team"]').trigger('click');
    await wrapper.get('textarea').setValue('联合研判');
    await wrapper.get('[data-testid="send-message"]').trigger('click'); await flushPromises();
    expect(store.sendMessage).toHaveBeenCalledWith('联合研判', 'team', 'team-1');
    expect(wrapper.text()).toContain('统筹研判'); expect(wrapper.text()).toContain('复核成果');
    wrapper.unmount();
  });

  it('shows an explicit empty state and prevents Team submission without a catalogue', async () => {
    const wrapper = mount(AgentConsoleView, { global: { stubs } }); await flushPromises();
    await wrapper.get('[data-testid="mode-team"]').trigger('click');
    expect(wrapper.text()).toContain('当前项目暂无可运行团队');
    await wrapper.get('textarea').setValue('联合研判');
    expect(wrapper.get('[data-testid="send-message"]').attributes('disabled')).toBeDefined();
    wrapper.unmount();
  });

  it('renders member task and partial synthesis progress without raw prompts', async () => {
    mocks.listTeams.mockResolvedValue([team]); mocks.getTeamVersion.mockResolvedValue(version);
    store.events = [
      { sequence: 2, event_type: 'team.task.completed', payload: { agent_id: 'review', task_id: 'task-1', prompt: 'SECRET_PROMPT' } },
      { sequence: 3, event_type: 'team.synthesis.completed', payload: { partial: true, trace: 'SECRET_TRACE' } },
    ];
    const wrapper = mount(AgentConsoleView, { global: { stubs } }); await flushPromises();
    await wrapper.get('[data-testid="mode-team"]').trigger('click');
    expect(wrapper.text()).toContain('review · 任务 task-1 已完成');
    expect(wrapper.text()).toContain('团队汇总已完成（部分完成）');
    expect(wrapper.text()).not.toContain('SECRET_');
    wrapper.unmount();
  });
});
