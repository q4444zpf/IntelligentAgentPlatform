// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import routesSource from '@/router/routes.ts?raw';
import SessionManagementView from './SessionManagementView.vue';

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  revoke: vi.fn(),
  revokeOthers: vi.fn(),
  replace: vi.fn(),
}));

vi.mock('@/api/auth', () => ({
  sessionApi: {
    list: mocks.list,
    revoke: mocks.revoke,
    revokeOthers: mocks.revokeOthers,
  },
}));
vi.mock('vue-router', () => ({ useRouter: () => ({ replace: mocks.replace }) }));

const stubs = {
  'a-card': { template: '<section><slot /><slot name="title" /></section>' },
  'a-space': { template: '<div><slot /></div>' },
  'a-typography-title': { template: '<h2><slot /></h2>' },
  'a-typography-text': { template: '<span><slot /></span>' },
  'a-alert': { props: ['message', 'description'], template: '<div class="alert">{{ message }} {{ description }}<slot name="action" /></div>' },
  'a-button': { props: ['loading', 'disabled'], emits: ['click'], template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>' },
  'a-tag': { template: '<span class="tag"><slot /></span>' },
  'a-popconfirm': { emits: ['confirm'], template: '<div><slot /><button class="confirm-revoke" @click="$emit(\'confirm\')">确认撤销</button></div>' },
  'a-table': { props: ['dataSource', 'loading', 'columns'], template: '<div><span v-if="loading">加载中</span><div v-for="row in dataSource" :key="row.session_id"><template v-for="column in columns" :key="column.key"><slot name="bodyCell" :record="row" :column="column" /></template></div><slot name="emptyText" /></div>' },
  'a-empty': { props: ['description'], template: '<div>{{ description }}</div>' },
};

const session = (overrides: Record<string, unknown> = {}) => ({
  session_id: 'session-current-123456',
  auth_method: 'local',
  created_at: '2026-08-08T01:02:03Z',
  last_seen_at: '2026-08-08T02:02:03Z',
  current_project: { id: 'project-1', name: '项目一' },
  is_current_session: true,
  ...overrides,
});

const wrappers: ReturnType<typeof mount>[] = [];
function render() {
  const wrapper = mount(SessionManagementView, { global: { stubs } });
  wrappers.push(wrapper);
  return wrapper;
}

beforeEach(() => {
  mocks.list.mockReset();
  mocks.revoke.mockReset();
  mocks.revokeOthers.mockReset();
  mocks.replace.mockReset();
});
afterEach(() => wrappers.splice(0).forEach((wrapper) => wrapper.unmount()));

describe('会话管理', () => {
  it('loads and renders masked sessions with the current marker', async () => {
    mocks.list.mockResolvedValue({ sessions: [session(), session({ session_id: 'session-other-abcdef', is_current_session: false, auth_method: 'oidc' })] });
    const wrapper = render();
    await flushPromises();
    expect(mocks.list).toHaveBeenCalledWith(expect.any(AbortSignal));
    expect(wrapper.text()).toContain('当前会话');
    expect(wrapper.text()).toContain('统一认证');
    expect(wrapper.text()).toContain('项目一');
    expect(wrapper.text()).not.toContain('session-current-123456');
    expect(wrapper.text()).toContain('session-...3456');
  });

  it('confirms and revokes a non-current session', async () => {
    mocks.list.mockResolvedValue({ sessions: [session(), session({ session_id: 'session-other-abcdef', is_current_session: false })] });
    mocks.revoke.mockResolvedValue({ status: 'ok', revoked: 1 });
    const wrapper = render();
    await flushPromises();
    await wrapper.findAll('.confirm-revoke')[1].trigger('click');
    await flushPromises();
    expect(mocks.revoke).toHaveBeenCalledWith('session-other-abcdef');
    expect(mocks.list).toHaveBeenCalledTimes(2);
  });

  it('offers revoke-others and shows API errors', async () => {
    mocks.list.mockResolvedValue({ sessions: [session(), session({ session_id: 'session-other-abcdef', is_current_session: false })] });
    mocks.revokeOthers.mockRejectedValue(new Error('会话服务不可用'));
    const wrapper = render();
    await flushPromises();
    await wrapper.get('[aria-label="撤销其他会话"]').trigger('click');
    await flushPromises();
    expect(mocks.revokeOthers).toHaveBeenCalledOnce();
    expect(wrapper.text()).toContain('会话服务不可用');
  });
});

it('routes system sessions to the real session management view', () => {
  expect(routesSource).toContain("path: 'system/sessions', name: 'Sessions', component: () => import('@/views/security/SessionManagementView.vue')");
});
