// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import McpManageView from './McpManageView.vue';

const mocks = vi.hoisted(() => ({
  list: vi.fn(), testConnection: vi.fn(), health: vi.fn(), projects: vi.fn(), updateProjects: vi.fn(), archive: vi.fn(), restore: vi.fn(),
  showSuccess: vi.fn(), showError: vi.fn(),
}));

vi.mock('@/api/mcp', () => ({ mcpApi: { ...mocks, create: vi.fn(), update: vi.fn(), toggle: vi.fn(), remove: vi.fn(), tools: vi.fn(), syncTools: vi.fn(), updateTools: vi.fn() } }));
vi.mock('ant-design-vue', () => ({ message: { success: mocks.showSuccess, error: mocks.showError } }));

const client = (status = 'active') => ({ key: 'water', client_id: 'water', name: '水情 MCP', description: '水情工具', transport: 'streamable_http', url: 'https://example.test/mcp', headers: {}, credential_id: null, command: '', args: [], env: {}, cwd: '', enabled: true, status, health_status: 'healthy', tools: null, tool_count: 2, enabled_tool_count: 2, last_synced_at: '2026-08-10T00:00:00Z', created_at: '2026-08-10T00:00:00Z', updated_at: '2026-08-10T00:00:00Z' });

const stubs = {
  'a-alert': { props: ['message', 'description'], template: '<div>{{ message }} {{ description }}</div>' },
  'a-space': { template: '<div><slot /></div>' },
  'a-input': { template: '<input />' }, 'a-select': { template: '<div />' },
  'a-tooltip': { template: '<span><slot /></span>' },
  'a-button': { emits: ['click'], template: '<button v-bind="$attrs" @click="$emit(\'click\')"><slot /></button>' },
  'a-tag': { template: '<span><slot /></span>' }, 'a-spin': { template: '<div><slot /></div>' }, 'a-empty': { template: '<div />' },
  'a-switch': { template: '<button />' }, 'a-popconfirm': { template: '<span><slot /></span>' },
  'a-modal': { template: '<div><slot /></div>' }, 'a-drawer': { template: '<div><slot /></div>' },
  'a-tabs': { template: '<div><slot /></div>' }, 'a-tab-pane': true, 'a-form': { template: '<form><slot /></form>' }, 'a-form-item': { template: '<div><slot /></div>' },
  'a-segmented': true, 'a-textarea': true, 'a-checkbox-group': { template: '<div><slot /></div>' }, 'a-checkbox': true,
  ApiOutlined: true, AppstoreOutlined: true, DeleteOutlined: true, PlusOutlined: true, ReloadOutlined: true, SearchOutlined: true, SettingOutlined: true, SyncOutlined: true,
};

const wrappers: ReturnType<typeof mount>[] = [];
function render() { const wrapper = mount(McpManageView, { global: { stubs } }); wrappers.push(wrapper); return wrapper; }

beforeEach(() => {
  Object.values(mocks).forEach((mock) => mock.mockReset());
  mocks.list.mockResolvedValue([client()]);
  mocks.testConnection.mockResolvedValue({ id: 'op-1', status: 'succeeded', phase: 'tools/list', result: { tool_count: 2 } });
  mocks.projects.mockResolvedValue({ project_ids: ['p1'] });
  mocks.updateProjects.mockResolvedValue({ project_ids: ['p1', 'p2'] });
  mocks.archive.mockResolvedValue(client('archived'));
  mocks.restore.mockResolvedValue(client('active'));
});
afterEach(() => wrappers.splice(0).forEach((wrapper) => wrapper.unmount()));

describe('McpManageView management flow', () => {
  it('shows health and runs a manual connection test', async () => {
    const wrapper = render(); await flushPromises();
    expect(wrapper.text()).toContain('健康');
    await wrapper.get('[aria-label="测试连接"]').trigger('click'); await flushPromises();
    expect(mocks.testConnection).toHaveBeenCalledWith('water');
    expect(mocks.showSuccess).toHaveBeenCalledWith('连接正常，发现 2 个工具');
  });

  it('loads project grants from the project authorization action', async () => {
    const wrapper = render(); await flushPromises();
    await wrapper.get('[aria-label="项目授权"]').trigger('click'); await flushPromises();
    expect(mocks.projects).toHaveBeenCalledWith('water');
    expect(wrapper.text()).toContain('p1');
  });

  it('archives and restores clients through explicit actions', async () => {
    const wrapper = render(); await flushPromises();
    await wrapper.get('[aria-label="归档 MCP"]').trigger('click'); await flushPromises();
    expect(mocks.archive).toHaveBeenCalledWith('water');
    mocks.list.mockResolvedValue([client('archived')]); await wrapper.get('[aria-label="刷新客户端列表"]').trigger('click'); await flushPromises();
    await wrapper.get('[aria-label="恢复 MCP"]').trigger('click'); await flushPromises();
    expect(mocks.restore).toHaveBeenCalledWith('water');
  });
});
