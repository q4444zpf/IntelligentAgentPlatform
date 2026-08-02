// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import routesSource from '@/router/routes.ts?raw';
import permissionSource from '@/stores/permission.ts?raw';
import ToolManageView from './ToolManageView.vue';
import source from './ToolManageView.vue?raw';

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  toggle: vi.fn(),
  showError: vi.fn(),
  showSuccess: vi.fn(),
}));

vi.mock('@/api/tools', () => ({
  toolsApi: { list: mocks.list, toggle: mocks.toggle },
}));

vi.mock('ant-design-vue', () => ({
  message: { error: mocks.showError, success: mocks.showSuccess },
}));

const { list, toggle, showError, showSuccess } = mocks;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function tool(tool_id: string, enabled = true) {
  return {
    tool_id,
    version: '1.0.0',
    name: tool_id,
    description: '测试工具',
    source: 'builtin',
    risk_level: 'low',
    input_schema: { properties: {} },
    output_schema: { properties: {} },
    requires_approval: false,
    published: true,
    enabled,
    is_builtin: true,
    created_at: '2026-08-02T00:00:00Z',
    updated_at: '2026-08-02T00:00:00Z',
  };
}

const stubs = {
  'a-alert': { props: ['description'], template: '<div class="alert">{{ description }}</div>' },
  'a-space': { template: '<div><slot /></div>' },
  'a-input-search': { template: '<input />' },
  'a-button': { emits: ['click'], template: '<button v-bind="$attrs" @click="$emit(\'click\')"><slot /></button>' },
  'a-select': { props: ['options'], template: '<div />' },
  'a-spin': { template: '<div><slot /></div>' },
  'a-tag': { template: '<span><slot /></span>' },
  'a-empty': { props: ['description'], template: '<div>{{ description }}</div>' },
  'a-switch': {
    props: ['checked', 'loading', 'disabled'],
    emits: ['change'],
    template: '<button class="tool-switch" :data-checked="checked" :data-loading="loading" :disabled="disabled" @click="$emit(\'change\', !checked)" />',
  },
  ApiOutlined: true,
  ReloadOutlined: true,
};

const wrappers: ReturnType<typeof mount>[] = [];

function render() {
  const wrapper = mount(ToolManageView, { global: { stubs } });
  wrappers.push(wrapper);
  return wrapper;
}

beforeEach(() => {
  list.mockReset();
  toggle.mockReset();
  showError.mockReset();
  showSuccess.mockReset();
});

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
});

describe('ToolManageView interactions', () => {
  it('loads and renders registered tools', async () => {
    list.mockResolvedValue([tool('system.get_current_time')]);

    const wrapper = render();
    await flushPromises();

    expect(list).toHaveBeenCalledOnce();
    expect(wrapper.text()).toContain('system.get_current_time');
    expect(wrapper.text()).toContain('系统内置');
  });

  it('shows a visible load error', async () => {
    list.mockRejectedValue(new Error('服务不可用'));

    const wrapper = render();
    await flushPromises();

    expect(wrapper.find('.alert').text()).toContain('服务不可用');
  });

  it('ignores a stale refresh result and stale cleanup', async () => {
    const first = deferred<ReturnType<typeof tool>[]>();
    const second = deferred<ReturnType<typeof tool>[]>();
    list.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const wrapper = render();
    await wrapper.get('[aria-label="刷新工具列表"]').trigger('click');

    second.resolve([tool('new-tool')]);
    await flushPromises();
    first.resolve([tool('stale-tool')]);
    await flushPromises();

    expect(wrapper.text()).toContain('new-tool');
    expect(wrapper.text()).not.toContain('stale-tool');
  });

  it('keeps state unchanged and reports a toggle failure', async () => {
    list.mockResolvedValue([tool('tool-one', true)]);
    toggle.mockRejectedValue(new Error('禁止修改'));
    const wrapper = render();
    await flushPromises();

    await wrapper.get('.tool-switch').trigger('click');
    await flushPromises();

    expect(wrapper.get('.tool-switch').attributes('data-checked')).toBe('true');
    expect(showError).toHaveBeenCalledWith('禁止修改');
  });

  it('tracks different tools independently and blocks duplicate toggles', async () => {
    list.mockResolvedValue([tool('tool-one'), tool('tool-two')]);
    const first = deferred<ReturnType<typeof tool>>();
    const second = deferred<ReturnType<typeof tool>>();
    toggle.mockImplementation((toolId: string) => toolId === 'tool-one' ? first.promise : second.promise);
    const wrapper = render();
    await flushPromises();
    const switches = wrapper.findAll('.tool-switch');

    await switches[0].trigger('click');
    await switches[0].trigger('click');
    await switches[1].trigger('click');

    expect(toggle).toHaveBeenCalledTimes(2);
    expect(switches[0].attributes('data-loading')).toBe('true');
    expect(switches[1].attributes('data-loading')).toBe('true');
    expect(switches[0].attributes('disabled')).toBeDefined();
    expect(switches[1].attributes('disabled')).toBeDefined();

    first.resolve(tool('tool-one', false));
    second.resolve(tool('tool-two', false));
    await flushPromises();
  });
});

describe('ToolManageView contract', () => {
  it('keeps registry restrictions, route and permissions explicit', () => {
    expect(source).toContain('riskLabel');
    expect(source).not.toContain('DeleteOutlined');
    expect(routesSource).toContain("path: '/tools'");
    expect(routesSource).toContain("path: 'tools'");
    expect(routesSource).toContain("permission: 'tool:view'");
    expect(permissionSource).toContain("'tool:view'");
  });
});
