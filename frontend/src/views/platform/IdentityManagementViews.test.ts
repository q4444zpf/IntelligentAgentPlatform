// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils';
import { nextTick } from 'vue';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import RoleManagementView from './RoleManagementView.vue';
import UserManagementView from './UserManagementView.vue';

const mocks = vi.hoisted(() => ({
  listUsers: vi.fn(),
  listRoles: vi.fn(),
  listProjects: vi.fn(),
  updateUser: vi.fn(),
  resetPassword: vi.fn(),
  listUserRoles: vi.fn(),
  replaceUserRoles: vi.fn(),
  deleteRole: vi.fn(),
  listPermissions: vi.fn(),
  grantRolePermission: vi.fn(),
}));
vi.mock('@/api/identity', () => ({
  listIdentityUsers: mocks.listUsers,
  listIdentityRoles: mocks.listRoles,
  listIdentityProjects: mocks.listProjects,
  updateIdentityUser: mocks.updateUser,
  resetIdentityUserPassword: mocks.resetPassword,
  listIdentityUserRoles: mocks.listUserRoles,
  replaceIdentityUserRoles: mocks.replaceUserRoles,
  deleteIdentityRole: mocks.deleteRole,
  listIdentityPermissions: mocks.listPermissions,
  grantIdentityRolePermission: mocks.grantRolePermission,
}));

const stubs = {
  'a-card': { template: '<section><slot /><slot name="title" /></section>' },
  'a-row': { template: '<div><slot /></div>' }, 'a-col': { template: '<div><slot /></div>' },
  'a-space': { template: '<div><slot /></div>' }, 'a-typography-title': { template: '<h4><slot /></h4>' },
  'a-typography-text': { template: '<span><slot /></span>' }, 'a-tag': { template: '<span><slot /></span>' },
  'a-alert': { props: ['message'], template: '<div>{{ message }}</div>' }, 'a-empty': { props: ['description'], template: '<div>{{ description }}</div>' },
  'a-button': { emits: ['click'], template: '<button @click="$emit(\'click\')"><slot /></button>' },
  'a-popconfirm': { emits: ['confirm'], template: '<div><slot /><slot name="title" /><button @click="$emit(\'confirm\')">确认删除</button></div>' },
  'a-input': { props: ['value'], emits: ['update:value'], template: '<input :value="value" @input="$emit(\'update:value\', $event.target.value)" />' },
  'a-input-password': { props: ['value'], emits: ['update:value'], template: '<input type="password" :value="value" @input="$emit(\'update:value\', $event.target.value)" />' },
  'a-form': { template: '<form><slot /></form>' },
  'a-form-item': { template: '<div><slot /></div>' },
  'a-modal': { props: ['open'], emits: ['ok'], template: '<div v-if="open"><slot /><slot name="title" /><button @click="$emit(\'ok\')">保存</button></div>' },
  'a-select': { props: ['value', 'options'], emits: ['update:value', 'change'], template: '<select :value="value" @change="$emit(\'update:value\', $event.target.value); $emit(\'change\', $event.target.value)"><option v-for="option in options || []" :key="option.value" :value="option.value">{{ option.label }}</option><slot /></select>' },
  'a-table': { props: ['dataSource', 'loading', 'columns'], template: '<div><span v-if="loading">加载中</span><div v-for="row in dataSource" :key="row.id">{{ row.display_name || row.name }}<template v-for="column in columns" :key="column.key || column.dataIndex"><slot name="bodyCell" :column="column" :record="row" /></template></div><slot name="emptyText" /></div>' },
};
const wrappers: ReturnType<typeof mount>[] = [];
function render(component: typeof UserManagementView | typeof RoleManagementView) {
  const wrapper = mount(component, { global: { stubs } }); wrappers.push(wrapper); return wrapper;
}

beforeEach(() => { mocks.listUsers.mockReset(); mocks.listRoles.mockReset(); mocks.listProjects.mockReset(); mocks.updateUser.mockReset(); mocks.resetPassword.mockReset(); mocks.listUserRoles.mockReset(); mocks.replaceUserRoles.mockReset(); mocks.deleteRole.mockReset(); mocks.listPermissions.mockReset(); mocks.grantRolePermission.mockReset(); });
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

  it('protects built-in roles from destructive operations', async () => {
    mocks.listRoles.mockResolvedValue([{ id: 'role-1', code: 'unit_admin', name: '单位管理员', scope_type: 'unit', unit_id: 'unit-1', built_in: true, status: 'active' }]);
    const wrapper = render(RoleManagementView);
    await flushPromises();
    expect(wrapper.text()).toContain('内置角色不可删除');
    expect(wrapper.findAll('a').map((link) => link.text())).not.toContain('删除');
  });

  it('deletes a custom role after confirmation', async () => {
    mocks.listRoles.mockResolvedValue([{ id: 'role-2', code: 'operator', name: '调度员', scope_type: 'unit', unit_id: 'unit-1', built_in: false, status: 'active' }]);
    mocks.deleteRole.mockResolvedValue({ role_id: 'role-2', deleted: true });
    const wrapper = render(RoleManagementView);
    await flushPromises();
    await wrapper.findAll('button').find((button) => button.text() === '确认删除')!.trigger('click');
    await flushPromises();
    expect(mocks.deleteRole).toHaveBeenCalledWith('role-2');
  });

  it('opens the permission catalogue and grants an available permission', async () => {
    mocks.listRoles.mockResolvedValue([{ id: 'role-1', code: 'operator', name: '调度员', scope_type: 'unit', unit_id: 'unit-1', built_in: false, status: 'active' }]);
    mocks.listPermissions.mockResolvedValue([{ id: 'permission-1', code: 'system:users.read', resource: 'system:users', action: 'read', risk_level: 'low', status: 'active' }]);
    mocks.grantRolePermission.mockResolvedValue({ role_id: 'role-1', permission_code: 'system:users.read', data_scope: 'unit' });
    const wrapper = render(RoleManagementView);
    await flushPromises();
    await wrapper.findAll('a').find((link) => link.text() === '权限管理')!.trigger('click');
    await flushPromises();
    expect(wrapper.text()).toContain('权限目录');
    await wrapper.find('select').setValue('system:users.read');
    await wrapper.findAll('button').find((button) => button.text() === '授权')!.trigger('click');
    await flushPromises();
    expect(mocks.grantRolePermission).toHaveBeenCalledWith('role-1', { permission_code: 'system:users.read', data_scope: 'unit' });
  });

  it('provides an edit action for an existing user', async () => {
    mocks.listUsers.mockResolvedValue([{ id: 'user-1', display_name: 'Alice', email: 'alice@example.test', status: 'active', membership_status: 'active', project_memberships: [], role_summaries: [] }]);
    const wrapper = render(UserManagementView);
    await flushPromises();
    expect(wrapper.text()).toContain('编辑');
  });

  it('shows password reset and role management actions for local users', async () => {
    mocks.listUsers.mockResolvedValue([{ id: 'user-1', display_name: 'Alice', email: 'alice@example.test', status: 'active', membership_status: 'active', project_memberships: [], role_summaries: [] }]);
    const wrapper = render(UserManagementView);
    await flushPromises();
    expect(wrapper.text()).toContain('重置密码');
    expect(wrapper.text()).toContain('角色管理');
  });

  it('resets a local user password from the operation dialog', async () => {
    mocks.listUsers.mockResolvedValue([{ id: 'user-1', display_name: 'Alice', email: 'alice@example.test', status: 'active', membership_status: 'active', project_memberships: [], role_summaries: [] }]);
    mocks.resetPassword.mockResolvedValue({ user_id: 'user-1', must_change_password: true });
    const wrapper = render(UserManagementView);
    await flushPromises();
    const resetLink = wrapper.findAll('a').find((link) => link.text() === '重置密码');
    expect(resetLink).toBeDefined();
    await resetLink!.trigger('click');
    expect(wrapper.text()).toContain('重置密码');
    const inputs = wrapper.findAll('input');
    await inputs[inputs.length - 1].setValue('NewPassword123!');
    await wrapper.findAll('button').find((button) => button.text() === '保存')?.trigger('click');
    await flushPromises();
    expect(mocks.resetPassword).toHaveBeenCalledWith('user-1', { new_password: 'NewPassword123!' });
  });

  it('loads and replaces the selected user roles', async () => {
    mocks.listUsers.mockResolvedValue([{ id: 'user-1', display_name: 'Alice', email: 'alice@example.test', status: 'active', membership_status: 'active', project_memberships: [], role_summaries: [], }]);
    mocks.listRoles.mockResolvedValue([{ id: 'role-1', code: 'unit_admin', name: '单位管理员', scope_type: 'unit', unit_id: 'unit-1', built_in: true, status: 'active' }]);
    mocks.listUserRoles.mockResolvedValue([{ role_id: 'role-1', code: 'unit_admin', name: '单位管理员', scope_type: 'unit', project_id: null }]);
    mocks.replaceUserRoles.mockResolvedValue([]);
    const wrapper = render(UserManagementView);
    await flushPromises();
    const roleLink = wrapper.findAll('a').find((link) => link.text() === '角色管理');
    expect(roleLink).toBeDefined();
    await roleLink!.trigger('click');
    await flushPromises();
    expect(mocks.listUserRoles).toHaveBeenCalledWith('user-1', null, expect.any(AbortSignal));
    await wrapper.findAll('button').find((button) => button.text() === '保存')?.trigger('click');
    await flushPromises();
    expect(mocks.replaceUserRoles).toHaveBeenCalledWith('user-1', { role_ids: ['role-1'], project_id: null });
  });

  it('hides local password operations for an OIDC user', async () => {
    mocks.listUsers.mockResolvedValue([{ id: 'user-1', display_name: 'Alice', email: 'alice@example.test', status: 'active', membership_status: 'active', project_memberships: [], role_summaries: [], auth_method: 'oidc' }]);
    const wrapper = render(UserManagementView);
    await flushPromises();
    expect(wrapper.text()).not.toContain('重置密码');
  });

  it('queries project-scoped roles after selecting a project', async () => {
    mocks.listUsers.mockResolvedValue([{ id: 'user-1', display_name: 'Alice', email: null, status: 'active', membership_status: 'active', project_memberships: [], role_summaries: [] }]);
    mocks.listRoles.mockResolvedValue([{ id: 'role-2', code: 'project_operator', name: '项目操作员', scope_type: 'project', unit_id: 'unit-1', built_in: false, status: 'active' }]);
    mocks.listProjects.mockResolvedValue([{ id: 'project-1', unit_id: 'unit-1', code: 'P1', name: '项目一', status: 'active' }]);
    mocks.listUserRoles.mockResolvedValue([]);
    const wrapper = render(UserManagementView);
    await flushPromises();
    await wrapper.findAll('a').find((link) => link.text() === '角色管理')!.trigger('click');
    await flushPromises();
    await wrapper.findAll('select')[0].setValue('project');
    await flushPromises();
    expect(mocks.listProjects).toHaveBeenCalled();
    expect(mocks.listUserRoles).toHaveBeenCalledWith('user-1', 'project-1', expect.any(AbortSignal));
  });
});
