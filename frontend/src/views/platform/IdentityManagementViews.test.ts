// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils';
import { nextTick } from 'vue';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import RoleManagementView from './RoleManagementView.vue';
import UserManagementView from './UserManagementView.vue';

const mocks = vi.hoisted(() => ({ listUsers: vi.fn(), listRoles: vi.fn() }));
vi.mock('@/api/identity', () => ({ listIdentityUsers: mocks.listUsers, listIdentityRoles: mocks.listRoles }));

const stubs = {
  'a-card': { template: '<section><slot /><slot name="title" /></section>' },
  'a-row': { template: '<div><slot /></div>' }, 'a-col': { template: '<div><slot /></div>' },
  'a-space': { template: '<div><slot /></div>' }, 'a-typography-title': { template: '<h4><slot /></h4>' },
  'a-typography-text': { template: '<span><slot /></span>' }, 'a-tag': { template: '<span><slot /></span>' },
  'a-alert': { props: ['message'], template: '<div>{{ message }}</div>' }, 'a-empty': { props: ['description'], template: '<div>{{ description }}</div>' },
  'a-button': { emits: ['click'], template: '<button @click="$emit(\'click\')"><slot /></button>' },
  'a-popconfirm': { emits: ['confirm'], template: '<div><slot /><slot name="title" /></div>' },
  'a-input': { props: ['value'], template: '<input />' },
  'a-form': { template: '<form><slot /></form>' },
  'a-form-item': { template: '<div><slot /></div>' },
  'a-modal': { props: ['open'], template: '<div v-if="open"><slot /><slot name="title" /></div>' },
  'a-select': { template: '<select><slot /></select>' },
  'a-table': { props: ['dataSource', 'loading'], template: '<div><span v-if="loading">加载中</span><div v-for="row in dataSource" :key="row.id">{{ row.display_name || row.name }}</div><slot name="emptyText" /></div>' },
};
const wrappers: ReturnType<typeof mount>[] = [];
function render(component: typeof UserManagementView | typeof RoleManagementView) {
  const wrapper = mount(component, { global: { stubs } }); wrappers.push(wrapper); return wrapper;
}

beforeEach(() => { mocks.listUsers.mockReset(); mocks.listRoles.mockReset(); });
afterEach(() => wrappers.splice(0).forEach((wrapper) => wrapper.unmount()));

describe('identity administration views', () => {
  it('shows loading and user data from the identity API', async () => {
    let resolve!: (value: unknown[]) => void;
    mocks.listUsers.mockReturnValue(new Promise((done) => { resolve = done; }));
    const wrapper = render(UserManagementView);
    await nextTick();
    expect(wrapper.text()).toContain('加载中');
    resolve([{ id: 'user-1', display_name: 'Alice', email: null, status: 'active', membership_status: 'active', project_memberships: [], role_summaries: [] }]);
    await flushPromises();
    expect(wrapper.text()).toContain('Alice');
  });

  it('shows an API failure on the roles page', async () => {
    mocks.listRoles.mockRejectedValue(new Error('角色服务不可用'));
    const wrapper = render(RoleManagementView);
    await flushPromises();
    expect(wrapper.text()).toContain('角色数据加载失败');
  });
});
