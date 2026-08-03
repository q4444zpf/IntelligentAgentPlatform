// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils';
import dayjs from 'dayjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import routesSource from '@/router/routes.ts?raw';
import AgentRunListView from './AgentRunListView.vue';

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  listEvents: vi.fn(),
  listInvocations: vi.fn(),
}));

vi.mock('@/api/agentRuns', () => ({ agentRunsApi: mocks }));

function run(id = 'run-1', status = 'completed') {
  return {
    id,
    conversation_id: 'conversation-1',
    trigger_message_id: 'message-1',
    actor_type: 'agent',
    actor_id: 'reservoir-agent',
    status,
    created_at: '2026-08-03T01:02:03Z',
    updated_at: '2026-08-03T01:02:05Z',
    conversation_title: '防洪调度会话',
    trigger_summary: '计算水库联合调度方案',
    tool_invocation_count: 1,
    duration_ms: 2040,
  };
}

function page(items = [run()]) {
  return {
    items,
    page: 1,
    page_size: 20,
    total: items.length,
    summary: { total: 9, completed: 5, running: 2, failed: 2, tool_invocations: 12 },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}

const stubs = {
  'a-alert': { props: ['description', 'message'], template: '<div class="alert">{{ message }} {{ description }}<slot name="action" /></div>' },
  'a-button': { props: ['loading'], emits: ['click'], template: '<button v-bind="$attrs" @click="$emit(\'click\')"><slot name="icon" /><slot /></button>' },
  'a-input-search': {
    props: ['value'], emits: ['update:value', 'search'],
    template: '<div><input class="run-search" :value="value" @input="$emit(\'update:value\', $event.target.value)" /><button class="run-search-submit" @click="$emit(\'search\', value)">search</button></div>',
  },
  'a-input': { props: ['value'], emits: ['update:value', 'pressEnter'], template: '<input class="actor-filter" :value="value" @input="$emit(\'update:value\', $event.target.value)" @keyup.enter="$emit(\'pressEnter\')" />' },
  'a-select': {
    props: ['value'], emits: ['update:value', 'change'],
    template: '<button class="status-filter" @click="$emit(\'update:value\', \'failed\'); $emit(\'change\', \'failed\')">{{ value }}</button>',
  },
  'a-range-picker': { name: 'RangePickerStub', props: ['value'], emits: ['update:value', 'change'], template: '<button class="date-filter">date</button>' },
  'a-spin': { template: '<div><slot /></div>' },
  'a-tag': { template: '<span class="tag"><slot /></span>' },
  'a-empty': { props: ['description'], template: '<div class="empty">{{ description }}</div>' },
  'a-pagination': {
    props: ['current', 'pageSize', 'total'], emits: ['change', 'showSizeChange'],
    template: '<div><button class="next-page" @click="$emit(\'change\', 2, pageSize)">next</button><button class="change-size" @click="$emit(\'showSizeChange\', 1, 50)">size</button></div>',
  },
  'a-drawer': {
    props: ['open'], emits: ['update:open', 'close'],
    template: '<aside v-if="open" class="drawer"><button class="close-drawer" @click="$emit(\'update:open\', false); $emit(\'close\')">close</button><slot /></aside>',
  },
  ReloadOutlined: true,
  EyeOutlined: true,
};

const wrappers: ReturnType<typeof mount>[] = [];
function render() {
  const wrapper = mount(AgentRunListView, { global: { stubs } });
  wrappers.push(wrapper);
  return wrapper;
}

beforeEach(() => {
  Object.values(mocks).forEach((mock) => mock.mockReset());
  mocks.list.mockResolvedValue(page());
  mocks.get.mockResolvedValue(run());
  mocks.listEvents.mockResolvedValue([{ sequence: 1, event_type: 'run.started', payload: { source: '<operator>' } }]);
  mocks.listInvocations.mockResolvedValue([{ id: 'invoke-1', run_id: 'run-1', tool_call_id: 'call-1', tool_id: 'model.execute', tool_version: '2.1', status: 'completed', arguments_summary: { basin: '<北江>' }, result_summary: { ok: true }, duration_ms: 300, error_code: null, created_at: '', completed_at: '' }]);
});

afterEach(() => wrappers.splice(0).forEach((wrapper) => wrapper.unmount()));

describe('AgentRunListView list behavior', () => {
  it('loads the first server page and renders summary plus rows', async () => {
    const wrapper = render();
    await flushPromises();

    expect(mocks.list).toHaveBeenCalledWith({ page: 1, page_size: 20 }, expect.any(AbortSignal));
    expect(wrapper.text()).toContain('Agent Runs');
    expect(wrapper.text()).toContain('防洪调度会话');
    expect(wrapper.text()).toContain('12');
  });

  it('resets page one and requests again when a filter changes', async () => {
    const wrapper = render(); await flushPromises();
    await wrapper.get('.next-page').trigger('click'); await flushPromises();
    expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }), expect.any(AbortSignal));

    await wrapper.get('.status-filter').trigger('click'); await flushPromises();
    expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, status: 'failed' }), expect.any(AbortSignal));
  });


  it('resets page for actor, query and full-day ISO date filters', async () => {
    const wrapper = render(); await flushPromises();
    await wrapper.get('.next-page').trigger('click'); await flushPromises();
    await wrapper.get('.actor-filter').setValue('dispatch-agent');
    await wrapper.get('.actor-filter').trigger('keyup.enter'); await flushPromises();
    expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, actor_id: 'dispatch-agent' }), expect.any(AbortSignal));
    await wrapper.get('.next-page').trigger('click'); await flushPromises();
    await wrapper.get('.run-search').setValue('run-2026');
    await wrapper.get('.run-search-submit').trigger('click'); await flushPromises();
    expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, query: 'run-2026' }), expect.any(AbortSignal));
    await wrapper.get('.next-page').trigger('click'); await flushPromises();
    wrapper.findComponent({ name: 'RangePickerStub' }).vm.$emit('change', [dayjs('2026-08-01'), dayjs('2026-08-03')], ['2026-08-01', '2026-08-03']);
    await flushPromises();
    expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({
      page: 1,
      started_after: dayjs('2026-08-01').startOf('day').toISOString(),
      started_before: dayjs('2026-08-03').endOf('day').toISOString(),
    }), expect.any(AbortSignal));
  });

  it('refreshes the list and the selected open run', async () => {
    const wrapper = render(); await flushPromises();
    await wrapper.get('[aria-label="查看运行 run-1"]').trigger('click'); await flushPromises();
    await wrapper.get('[aria-label="刷新运行列表"]').trigger('click'); await flushPromises();
    expect(mocks.list).toHaveBeenCalledTimes(2);
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(mocks.listEvents).toHaveBeenCalledTimes(2);
    expect(mocks.listInvocations).toHaveBeenCalledTimes(2);
  });

  it('shows a retry action after a list failure', async () => {
    mocks.list.mockRejectedValueOnce(new Error('运行服务不可用')).mockResolvedValueOnce(page());
    const wrapper = render(); await flushPromises();
    expect(wrapper.text()).toContain('运行服务不可用');
    await wrapper.get('[aria-label="重试运行列表"]').trigger('click'); await flushPromises();
    expect(mocks.list).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).toContain('防洪调度会话');
  });

  it('aborts and ignores stale list responses', async () => {
    const first = deferred<ReturnType<typeof page>>();
    const second = deferred<ReturnType<typeof page>>();
    mocks.list.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const wrapper = render();
    const firstSignal = mocks.list.mock.calls[0][1] as AbortSignal;
    await wrapper.get('[aria-label="刷新运行列表"]').trigger('click');
    expect(firstSignal.aborted).toBe(true);
    second.resolve(page([run('new-run')])); await flushPromises();
    first.resolve(page([run('stale-run')])); await flushPromises();
    expect(wrapper.text()).toContain('new-run');
    expect(wrapper.text()).not.toContain('stale-run');
  });
});

describe('AgentRunListView details', () => {
  it('loads all detail resources only after opening a run and shows tools', async () => {
    const wrapper = render(); await flushPromises();
    expect(mocks.get).not.toHaveBeenCalled();
    await wrapper.get('[aria-label="查看运行 run-1"]').trigger('click');
    expect(mocks.get).toHaveBeenCalledWith('run-1', expect.any(AbortSignal));
    expect(mocks.listEvents).toHaveBeenCalledWith('run-1', expect.any(AbortSignal));
    expect(mocks.listInvocations).toHaveBeenCalledWith('run-1', expect.any(AbortSignal));
    await flushPromises();
    expect(wrapper.text()).toContain('run.started');
    expect(wrapper.text()).toContain('model.execute');
    expect(wrapper.text()).toContain('2.1');
    expect(wrapper.html()).toContain('&lt;北江&gt;');
  });

  it('renders the explicit no-tool state', async () => {
    mocks.listInvocations.mockResolvedValue([]);
    const wrapper = render(); await flushPromises();
    await wrapper.get('[aria-label="查看运行 run-1"]').trigger('click'); await flushPromises();
    expect(wrapper.text()).toContain('本次运行未调用工具');
  });

  it('keeps successful detail regions when events fail', async () => {
    mocks.listEvents.mockRejectedValue(new Error('事件流不可用'));
    const wrapper = render(); await flushPromises();
    await wrapper.get('[aria-label="查看运行 run-1"]').trigger('click'); await flushPromises();
    expect(wrapper.text()).toContain('事件流不可用');
    expect(wrapper.text()).toContain('model.execute');
    expect(wrapper.text()).toContain('reservoir-agent');
  });
});


  it('retries only a failed run detail and preserves successful sections', async () => {
    mocks.get.mockRejectedValueOnce(new Error('详情不可用')).mockResolvedValueOnce(run());
    const wrapper = render(); await flushPromises();
    await wrapper.get('[aria-label="查看运行 run-1"]').trigger('click'); await flushPromises();
    await wrapper.get('[aria-label="重试运行详情"]').trigger('click'); await flushPromises();
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(mocks.listEvents).toHaveBeenCalledOnce();
    expect(mocks.listInvocations).toHaveBeenCalledOnce();
    expect(wrapper.text()).toContain('reservoir-agent');
    expect(wrapper.text()).toContain('model.execute');
  });

  it('retries only failed events while keeping successful details', async () => {
    mocks.listEvents.mockRejectedValueOnce(new Error('事件流不可用')).mockResolvedValueOnce([{ sequence: 2, event_type: 'run.completed', payload: {} }]);
    const wrapper = render(); await flushPromises();
    await wrapper.get('[aria-label="查看运行 run-1"]').trigger('click'); await flushPromises();
    await wrapper.get('[aria-label="重试运行事件"]').trigger('click'); await flushPromises();
    expect(mocks.listEvents).toHaveBeenCalledTimes(2);
    expect(mocks.get).toHaveBeenCalledOnce();
    expect(mocks.listInvocations).toHaveBeenCalledOnce();
    expect(wrapper.text()).toContain('run.completed');
  });

  it('retries only failed tool invocations', async () => {
    mocks.listInvocations.mockRejectedValueOnce(new Error('工具记录不可用')).mockResolvedValueOnce([]);
    const wrapper = render(); await flushPromises();
    await wrapper.get('[aria-label="查看运行 run-1"]').trigger('click'); await flushPromises();
    await wrapper.get('[aria-label="重试工具调用"]').trigger('click'); await flushPromises();
    expect(mocks.listInvocations).toHaveBeenCalledTimes(2);
    expect(mocks.get).toHaveBeenCalledOnce();
    expect(mocks.listEvents).toHaveBeenCalledOnce();
    expect(wrapper.text()).toContain('本次运行未调用工具');
  });

  it('aborts old details and blocks stale data when switching runs', async () => {
    mocks.list.mockResolvedValue(page([run('run-1'), run('run-2')]));
    const oldRun = deferred<ReturnType<typeof run>>();
    mocks.get.mockImplementation((id: string) => id === 'run-1' ? oldRun.promise : Promise.resolve(run('run-2')));
    const wrapper = render(); await flushPromises();
    await wrapper.get('[aria-label="查看运行 run-1"]').trigger('click');
    const oldSignals = [mocks.get.mock.calls[0][1], mocks.listEvents.mock.calls[0][1], mocks.listInvocations.mock.calls[0][1]] as AbortSignal[];
    await wrapper.get('[aria-label="查看运行 run-2"]').trigger('click'); await flushPromises();
    expect(oldSignals.every((signal) => signal.aborted)).toBe(true);
    oldRun.resolve(run('stale-run')); await flushPromises();
    expect(wrapper.text()).toContain('run-2');
    expect(wrapper.text()).not.toContain('stale-run');
  });

  it('aborts open detail requests when the drawer closes', async () => {
    const pending = deferred<ReturnType<typeof run>>();
    mocks.get.mockReturnValue(pending.promise);
    const wrapper = render(); await flushPromises();
    await wrapper.get('[aria-label="查看运行 run-1"]').trigger('click');
    const signals = [mocks.get.mock.calls[0][1], mocks.listEvents.mock.calls[0][1], mocks.listInvocations.mock.calls[0][1]] as AbortSignal[];
    await wrapper.get('.close-drawer').trigger('click');
    expect(signals.every((signal) => signal.aborted)).toBe(true);
  });

  it('keeps the view button as the only row action', async () => {
    const wrapper = render(); await flushPromises();
    expect(wrapper.findAll('tbody button')).toHaveLength(1);
    expect(wrapper.get('tbody button').attributes('aria-label')).toBe('查看运行 run-1');
  });

describe('AgentRunListView route contract', () => {
  it('routes runs directly to the lazy audit view without a collaboration module', () => {
    expect(routesSource).toContain("path: 'runs', name: 'AgentRuns', component: () => import('@/views/runs/AgentRunListView.vue')");
    expect(routesSource).not.toContain("path: 'runs', name: 'AgentRuns', component: genericView");
  });
});
