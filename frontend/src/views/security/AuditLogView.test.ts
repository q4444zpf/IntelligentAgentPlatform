// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils';
import dayjs from 'dayjs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/api/client';
import routesSource from '@/router/routes.ts?raw';
import AuditLogView from './AuditLogView.vue';

const mocks = vi.hoisted(() => ({ list: vi.fn(), get: vi.fn(), related: vi.fn(), push: vi.fn() }));
vi.mock('@/api/audit', () => ({ auditApi: { list: mocks.list, get: mocks.get, related: mocks.related } }));
vi.mock('vue-router', () => ({ useRouter: () => ({ push: mocks.push }) }));

const event = (id = 'audit-1', overrides = {}) => ({ id, unit_id: 'unit-1', project_id: 'project-1', user_id: 'operator-1', actor_roles: ['project_admin', 'user'], authorization_scope: 'project', event_scope: 'project', auth_method: null, category: 'runtime', source: 'agent', action: 'agent.run', status: 'succeeded', risk_level: 'low', trace_id: 'trace-1', run_id: 'run-1', resource_type: 'agent', resource_id: 'reservoir-agent', resource_name: '水库调度智能体', duration_ms: 320, occurred_at: '2026-08-03T01:02:03Z', ...overrides });
const page = (items = [event()]) => ({ items, page: 1, page_size: 20, total: items.length, summary: { total: 12, failed: 2, high_risk: 3, runtime: 9, management: 3, by_source: { agent: 7 } } });
const detail = (id = 'audit-1') => ({ ...event(id), parent_event_id: null, summary: '<script>unsafe</script>', metadata: { token: '[REDACTED]' }, error_code: null, created_at: '2026-08-03T01:02:04Z' });
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason?: unknown) => void; const promise = new Promise<T>((ok, fail) => { resolve = ok; reject = fail; }); return { promise, resolve, reject }; }

const stubs = {
  'a-alert': { props: ['description', 'message'], template: '<div class="alert">{{ message }} {{ description }}<slot name="action" /></div>' },
  'a-input': { props: ['value', 'placeholder'], emits: ['update:value', 'pressEnter'], template: '<input :data-placeholder="placeholder" :value="value" @input="$emit(\'update:value\', $event.target.value)" @keyup.enter="$emit(\'pressEnter\')" />' },
  'a-button': { emits: ['click'], template: '<button v-bind="$attrs" @click="$emit(\'click\')"><slot name="icon" /><slot /></button>' },
  'a-input-search': { inheritAttrs: false, props: ['value'], emits: ['update:value', 'search'], template: '<div><input v-bind="$attrs" class="audit-search" :value="value" @input="$emit(\'update:value\', $event.target.value)"/><button class="search-submit" @click="$emit(\'search\', value)">search</button></div>' },
  'a-select': { name: 'SelectStub', props: ['value', 'dataTest'], emits: ['update:value', 'change'], template: '<button :class="dataTest" @click="$emit(\'update:value\', dataTest === \'category-filter\' ? \'management\' : \'failed\'); $emit(\'change\')">{{ value }}</button>' },
  'a-range-picker': { name: 'RangePickerStub', emits: ['change'], template: '<button v-bind="$attrs" class="date-filter">date</button>' },
  'a-spin': { props: ['spinning'], template: '<div class="spin" :data-spinning="spinning"><slot /></div>' },
  'a-tag': { template: '<span class="tag"><slot /></span>' }, 'a-empty': { props: ['description'], template: '<div>{{ description }}</div>' },
  'a-pagination': { props: ['current', 'pageSize'], emits: ['change', 'showSizeChange'], template: '<div><button class="next-page" @click="$emit(\'change\', 2, pageSize)">next</button><button class="change-size" @click="$emit(\'showSizeChange\', current, 50); $emit(\'change\', current, 50)">size</button></div>' },
  'a-drawer': { props: ['open'], emits: ['update:open', 'close'], template: '<aside v-if="open" class="drawer"><button class="close-drawer" @click="$emit(\'update:open\', false); $emit(\'close\')">close</button><slot /></aside>' },
  ReloadOutlined: true, EyeOutlined: true, LinkOutlined: true,
};
const wrappers: ReturnType<typeof mount>[] = [];
function render() { const wrapper = mount(AuditLogView, { global: { stubs } }); wrappers.push(wrapper); return wrapper; }
beforeEach(() => { vi.stubEnv('VITE_DEV_USER_ROLES', 'unit_auditor'); Object.values(mocks).forEach((mock) => mock.mockReset()); mocks.list.mockResolvedValue(page()); mocks.get.mockResolvedValue(detail()); mocks.related.mockResolvedValue([event()]); });
afterEach(() => wrappers.splice(0).forEach((wrapper) => wrapper.unmount()));

describe('AuditLogView list', () => {
  it('gives the filter group and every control a unique persistent accessible name', () => {
    const wrapper = render();
    expect(wrapper.get('section.filter-bar').attributes('aria-label')).toBe('筛选审计事件');
    const labels = [
      '审计类别', '审计来源', '审计状态', '风险等级', '操作类型',
      '项目 ID', '用户 ID', '发生日期', '关键词',
    ];

    for (const label of labels) {
      const controls = wrapper.findAll(`[aria-label="${label}"]`);
      expect(controls, label).toHaveLength(1);
      expect(['INPUT', 'BUTTON']).toContain(controls[0].element.tagName);
    }
    expect(new Set(labels).size).toBe(labels.length);
  });

  it('renders all specified audit columns with reachable values', async () => {
    const wrapper = render(); await flushPromises();
    expect(wrapper.findAll('th').map((node) => node.text())).toEqual([
      '时间', '类别', '来源', '动作', '操作人', '项目', '对象', '结果', '风险', '耗时', '详情',
    ]);
    expect(wrapper.text()).toContain('operator-1');
    expect(wrapper.text()).toContain('project_admin, user');
    expect(wrapper.text()).toContain('project-1');
    expect(wrapper.text()).toContain('320 ms');
  });

  it('renders an unknown legacy role snapshot without an administrator fallback', async () => {
    mocks.list.mockResolvedValue(page([event('unknown-role', { actor_roles: [] })]));

    const wrapper = render(); await flushPromises();

    expect(wrapper.get('tbody tr').findAll('td')[4].find('small').text()).toBe('-');
    expect(wrapper.get('tbody tr').text()).not.toContain('admin');
  });

  it.each([
    ['unit_auditor', true, true],
    ['project_admin', false, true],
    ['user', false, false],
  ])('controls scope filters for %s', async (roles, projectVisible, userVisible) => {
    vi.stubEnv('VITE_DEV_USER_ROLES', roles); const wrapper = render(); await flushPromises();
    expect(wrapper.find('[aria-label="项目 ID"]').exists()).toBe(projectVisible);
    expect(wrapper.find('[aria-label="用户 ID"]').exists()).toBe(userVisible);
  });

  it('loads the first page with its response summary', async () => {
    const wrapper = render(); await flushPromises();
    expect(mocks.list).toHaveBeenCalledWith({ page: 1, page_size: 20 }, expect.any(AbortSignal));
    expect(wrapper.text()).toContain('统一审计中心'); expect(wrapper.text()).toContain('12');
  });

  it('resets page and sends exactly one request per filter interaction', async () => {
    const wrapper = render(); await flushPromises(); await wrapper.get('.next-page').trigger('click'); await flushPromises();
    let before = mocks.list.mock.calls.length; await wrapper.get('.category-filter').trigger('click'); await flushPromises();
    expect(mocks.list.mock.calls.length - before).toBe(1); expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, category: 'management' }), expect.any(AbortSignal));
    before = mocks.list.mock.calls.length; await wrapper.get('.audit-search').setValue('trace-9'); await wrapper.get('.search-submit').trigger('click'); await flushPromises();
    expect(mocks.list.mock.calls.length - before).toBe(1); expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, query: 'trace-9' }), expect.any(AbortSignal));
    before = mocks.list.mock.calls.length; wrapper.findComponent({ name: 'RangePickerStub' }).vm.$emit('change', [dayjs('2026-08-01'), dayjs('2026-08-03')]); await flushPromises();
    expect(mocks.list.mock.calls.length - before).toBe(1); expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, occurred_after: dayjs('2026-08-01').startOf('day').toISOString(), occurred_before: dayjs('2026-08-03').endOf('day').toISOString() }), expect.any(AbortSignal));
  });

  it.each([
    ['.source-filter', 'source', 'tool'],
    ['.status-filter', 'status', 'failed'],
    ['.risk-filter', 'risk_level', 'high'],
  ])('applies %s once and resets the page', async (selector, key, value) => {
    const wrapper = render(); await flushPromises(); await wrapper.get('.next-page').trigger('click'); await flushPromises(); const before = mocks.list.mock.calls.length;
    const select = wrapper.findAllComponents({ name: 'SelectStub' }).find((item) => item.attributes('class')?.includes(selector.slice(1)));
    select?.vm.$emit('update:value', value); select?.vm.$emit('change'); await flushPromises();
    expect(mocks.list.mock.calls.length - before).toBe(1); expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, [key]: value }), expect.any(AbortSignal));
  });

  it.each([
    ['操作类型', 'action', 'resource.updated'],
    ['项目 ID', 'project_id', 'project-2'],
    ['用户 ID', 'user_id', 'operator-2'],
  ])('applies the %s text filter once and resets the page', async (placeholder, key, value) => {
    const wrapper = render(); await flushPromises(); await wrapper.get('.next-page').trigger('click'); await flushPromises(); const before = mocks.list.mock.calls.length;
    const input = wrapper.get(`[data-placeholder="${placeholder}"]`); await input.setValue(value); await input.trigger('keyup.enter'); await flushPromises();
    expect(mocks.list.mock.calls.length - before).toBe(1); expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, [key]: value }), expect.any(AbortSignal));
  });

  it('handles Ant page-size dual events with exactly one request', async () => {
    const wrapper = render(); await flushPromises(); const before = mocks.list.mock.calls.length; await wrapper.get('.change-size').trigger('click'); await flushPromises();
    expect(mocks.list.mock.calls.length - before).toBe(1); expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, page_size: 50 }), expect.any(AbortSignal));
  });

  it('aborts and ignores stale list responses', async () => {
    const old = deferred<ReturnType<typeof page>>(), fresh = deferred<ReturnType<typeof page>>(); mocks.list.mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise);
    const wrapper = render(); const signal = mocks.list.mock.calls[0][1] as AbortSignal; await wrapper.get('[aria-label="刷新审计列表"]').trigger('click'); expect(signal.aborted).toBe(true);
    fresh.resolve(page([event('fresh')])); await flushPromises(); old.resolve(page([event('stale')])); await flushPromises(); expect(wrapper.text()).toContain('fresh'); expect(wrapper.text()).not.toContain('stale');
  });
});

describe('AuditLogView details', () => {
  it('lazy loads detail and related records, escapes fields and sorts trace stably', async () => {
    mocks.related.mockResolvedValue([event('later', { occurred_at: '2026-08-03T02:00:00Z' }), event('first', { occurred_at: '2026-08-03T01:00:00Z' }), event('same-b', { occurred_at: '2026-08-03T03:00:00Z' }), event('same-a', { occurred_at: '2026-08-03T03:00:00Z' })]);
    const wrapper = render(); await flushPromises(); expect(mocks.get).not.toHaveBeenCalled(); await wrapper.get('[aria-label="查看审计事件 audit-1"]').trigger('click'); await flushPromises();
    expect(mocks.get).toHaveBeenCalledWith('audit-1', expect.any(AbortSignal)); expect(mocks.related).toHaveBeenCalledWith('audit-1', expect.any(AbortSignal)); expect(wrapper.html()).toContain('&lt;script&gt;unsafe&lt;/script&gt;'); expect(wrapper.text()).toContain('[REDACTED]');
    expect(wrapper.findAll('.trace-item').map((node) => node.attributes('data-event-id'))).toEqual(['first', 'later', 'same-a', 'same-b']);
  });

  it('keeps errors independent and retries only the failed region', async () => {
    mocks.related.mockRejectedValueOnce(new Error('关联链路不可用')).mockResolvedValueOnce([]); const wrapper = render(); await flushPromises(); await wrapper.get('[aria-label="查看审计事件 audit-1"]').trigger('click'); await flushPromises();
    expect(wrapper.text()).toContain('关联链路不可用'); expect(wrapper.text()).toContain('[REDACTED]'); await wrapper.get('[aria-label="重试关联事件"]').trigger('click'); await flushPromises(); expect(mocks.related).toHaveBeenCalledTimes(2); expect(mocks.get).toHaveBeenCalledOnce();
  });

  it('retries only detail failures', async () => {
    mocks.get.mockRejectedValueOnce(new Error('详情不可用')).mockResolvedValueOnce(detail()); const wrapper = render(); await flushPromises(); await wrapper.get('[aria-label="查看审计事件 audit-1"]').trigger('click'); await flushPromises();
    await wrapper.get('[aria-label="重试审计详情"]').trigger('click'); await flushPromises(); expect(mocks.get).toHaveBeenCalledTimes(2); expect(mocks.related).toHaveBeenCalledOnce();
  });

  it('reuses fresh per-event detail and timeline cache after close and reopen', async () => {
    const wrapper = render(); await flushPromises();
    await wrapper.get('[aria-label="查看审计事件 audit-1"]').trigger('click'); await flushPromises();
    await wrapper.get('.close-drawer').trigger('click');
    await wrapper.get('[aria-label="查看审计事件 audit-1"]').trigger('click'); await flushPromises();
    expect(mocks.get).toHaveBeenCalledOnce(); expect(mocks.related).toHaveBeenCalledOnce();
  });

  it('uses a safe 404 message and neutral labels for unknown enums', async () => {
    mocks.list.mockResolvedValue(page([event('audit-1', { status: 'future', risk_level: 'urgent', source: 'other' })])); mocks.get.mockRejectedValue(new ApiError('secret backend detail', 404)); const wrapper = render(); await flushPromises();
    expect(wrapper.text()).toContain('未知'); await wrapper.get('[aria-label="查看审计事件 audit-1"]').trigger('click'); await flushPromises(); expect(wrapper.text()).toContain('记录不存在或无权访问'); expect(wrapper.text()).not.toContain('secret backend detail');
  });

  it('aborts all pending resources on close and prevents stale replacement', async () => {
    const pendingDetail = deferred<ReturnType<typeof detail>>(), pendingRelated = deferred<ReturnType<typeof event>[]>(); mocks.get.mockReturnValue(pendingDetail.promise); mocks.related.mockReturnValue(pendingRelated.promise); const wrapper = render(); await flushPromises(); await wrapper.get('[aria-label="查看审计事件 audit-1"]').trigger('click');
    const signals = [mocks.get.mock.calls[0][1], mocks.related.mock.calls[0][1]] as AbortSignal[]; await wrapper.get('.close-drawer').trigger('click'); expect(signals.every((signal) => signal.aborted)).toBe(true); pendingDetail.resolve(detail('stale')); pendingRelated.resolve([event('stale')]); await flushPromises(); expect(wrapper.find('.drawer').exists()).toBe(false);
  });

  it('aborts old resources when switching events', async () => {
    mocks.list.mockResolvedValue(page([event('audit-1'), event('audit-2')])); const oldDetail = deferred<ReturnType<typeof detail>>(), oldRelated = deferred<ReturnType<typeof event>[]>(); mocks.get.mockReturnValueOnce(oldDetail.promise).mockResolvedValueOnce(detail('audit-2')); mocks.related.mockReturnValueOnce(oldRelated.promise).mockResolvedValueOnce([event('audit-2')]);
    const wrapper = render(); await flushPromises(); await wrapper.get('[aria-label="查看审计事件 audit-1"]').trigger('click'); const signals = [mocks.get.mock.calls[0][1], mocks.related.mock.calls[0][1]] as AbortSignal[]; await wrapper.get('[aria-label="查看审计事件 audit-2"]').trigger('click'); await flushPromises(); expect(signals.every((signal) => signal.aborted)).toBe(true); oldDetail.resolve(detail('stale')); oldRelated.resolve([event('stale')]); await flushPromises(); expect(wrapper.text()).not.toContain('stale');
  });

  it('links agent events with run ids to the run drawer query', async () => {
    const wrapper = render(); await flushPromises(); await wrapper.get('[aria-label="打开运行 run-1"]').trigger('click'); expect(mocks.push).toHaveBeenCalledWith({ path: '/runs', query: { run_id: 'run-1' } });
  });
});

it('routes system audit to the real lazy view', () => {
  expect(routesSource).toContain("path: 'system/audit', name: 'Audit', component: () => import('@/views/security/AuditLogView.vue')");
  expect(routesSource).not.toContain("path: 'system/audit', name: 'Audit', component: genericView");
});
