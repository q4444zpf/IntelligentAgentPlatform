// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import ApprovalListView from './ApprovalListView.vue';

const mocks = vi.hoisted(() => ({ list: vi.fn(), approve: vi.fn(), reject: vi.fn() }));
vi.mock('@/api/approvals', () => ({ approvalsApi: mocks }));

const stubs = {
  'a-card': { template: '<section><slot /><slot name="title" /></section>' },
  'a-space': { template: '<div><slot /></div>' },
  'a-typography-title': { template: '<h2><slot /></h2>' },
  'a-typography-text': { template: '<span><slot /></span>' },
  'a-alert': { props: ['message'], template: '<div class="alert">{{ message }}</div>' },
  'a-button': { props: ['loading', 'disabled'], emits: ['click'], template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>' },
  'a-select': { props: ['value', 'options'], emits: ['change'], template: '<select><option value="pending">待审批</option><option value="all">全部</option></select>' },
  'a-tag': { template: '<span><slot /></span>' },
  'a-table': { props: ['dataSource', 'loading', 'columns'], template: '<div><span v-if="loading">加载中</span><div v-for="row in dataSource" :key="row.id"><span>{{ row.tool_id }}</span><span>{{ row.status }}</span><template v-for="column in columns" :key="column.key"><slot name="bodyCell" :record="row" :column="column" /></template></div><slot name="emptyText" /></div>' },
  'a-empty': { props: ['description'], template: '<div>{{ description }}</div>' },
  'a-popconfirm': { emits: ['confirm'], template: '<div><slot /><button class="confirm" @click="$emit(\'confirm\')">确认</button></div>' },
};

const approval = { id: 'a1', run_id: 'run-1', invocation_id: 'i1', tool_id: 'water.release', tool_version: '1', unit_id: 'u1', project_id: 'p1', requester_id: 'requester', requester_roles: ['user'], assignee_role: 'project_admin', risk_level: 'high', arguments_summary: { amount: 10 }, arguments_digest: 'd', status: 'pending', reason: '高风险调度', decided_by: null, decision_reason: null, expires_at: '2026-08-11T00:00:00Z', decided_at: null, created_at: '2026-08-10T00:00:00Z', updated_at: '2026-08-10T00:00:00Z' };

const wrappers: ReturnType<typeof mount>[] = [];
beforeEach(() => { vi.clearAllMocks(); mocks.list.mockResolvedValue([approval]); mocks.approve.mockResolvedValue({ ...approval, status: 'approved' }); mocks.reject.mockResolvedValue({ ...approval, status: 'rejected' }); });
afterEach(() => wrappers.splice(0).forEach((wrapper) => wrapper.unmount()));

describe('审批中心', () => {
  it('loads pending approvals and exposes the tool and risk', async () => {
    const wrapper = mount(ApprovalListView, { global: { stubs } }); wrappers.push(wrapper);
    await flushPromises();
    expect(mocks.list).toHaveBeenCalledWith('pending', expect.any(AbortSignal));
    expect(wrapper.text()).toContain('water.release');
    expect(wrapper.text()).toContain('高风险');
  });

  it('approves an item and refreshes the list', async () => {
    const wrapper = mount(ApprovalListView, { global: { stubs } }); wrappers.push(wrapper);
    await flushPromises();
    await wrapper.get('[aria-label="批准审批 a1"]').trigger('click');
    await flushPromises();
    expect(mocks.approve).toHaveBeenCalledWith('a1');
    expect(mocks.list).toHaveBeenCalledTimes(2);
  });

  it('shows API errors', async () => {
    mocks.list.mockRejectedValueOnce(new Error('审批服务不可用'));
    const wrapper = mount(ApprovalListView, { global: { stubs } }); wrappers.push(wrapper);
    await flushPromises();
    expect(wrapper.text()).toContain('审批服务不可用');
  });
});
