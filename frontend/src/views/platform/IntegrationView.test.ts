// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils';
import { describe, expect, it, vi } from 'vitest';
import IntegrationView from './IntegrationView.vue';

const mocks = vi.hoisted(() => ({
  me: vi.fn(),
  list: vi.fn(),
}));
vi.mock('@/api/auth', () => ({ authApi: { me: mocks.me } }));
vi.mock('@/api/mcp', () => ({ mcpApi: { list: mocks.list } }));

const stubs = {
  'a-card': { template: '<section><slot /><slot name="title" /></section>' },
  'a-tag': { template: '<span><slot /></span>' },
  'a-alert': { props: ['message'], template: '<div>{{ message }}<slot /></div>' },
  'a-spin': { template: '<div><slot /></div>' },
};

describe('integration view', () => {
  it('renders real auth and MCP connection state', async () => {
    mocks.me.mockResolvedValue({ auth_method: 'local', user: { display_name: 'Alice' }, projects: [], current_project: null });
    mocks.list.mockResolvedValue([{ key: 'water', name: '水情 MCP', transport: 'streamable_http', enabled: true, tool_count: 3, enabled_tool_count: 3, last_synced_at: '2026-08-09T00:00:00Z' }]);
    const wrapper = mount(IntegrationView, { global: { stubs } });
    await flushPromises();
    expect(wrapper.text()).toContain('本地账号登录');
    expect(wrapper.text()).toContain('水情 MCP');
    expect(wrapper.text()).toContain('已连接');
  });

  it('explains when OIDC is not configured without exposing secrets', async () => {
    mocks.me.mockResolvedValue({ auth_method: 'dev_test', user: { display_name: '开发身份' }, projects: [], current_project: null });
    mocks.list.mockResolvedValue([]);
    const wrapper = mount(IntegrationView, { global: { stubs } });
    await flushPromises();
    expect(wrapper.text()).toContain('开发身份');
    expect(wrapper.text()).toContain('尚未配置');
    expect(wrapper.text()).not.toContain('secret');
  });
});
