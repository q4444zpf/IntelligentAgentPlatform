// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import TeamManageView from './TeamManageView.vue';

const mocks = vi.hoisted(() => ({
  list: vi.fn(), getVersion: vi.fn(), listVersions: vi.fn(), saveDraft: vi.fn(), publish: vi.fn(),
  create: vi.fn(), update: vi.fn(), setEnabled: vi.fn(), agents: vi.fn(), error: vi.fn(), success: vi.fn(),
  canManage: true,
}));
vi.mock('@/api/teams', () => ({ teamsApi: {
  list: mocks.list, getVersion: mocks.getVersion, listVersions: mocks.listVersions,
  saveDraft: mocks.saveDraft, publish: mocks.publish, create: mocks.create,
  update: mocks.update, setEnabled: mocks.setEnabled,
} }));
vi.mock('@/api/agents', () => ({ agentsApi: { list: mocks.agents } }));
vi.mock('@/stores/permission', () => ({ usePermissionStore: () => ({ get isAdmin() { return mocks.canManage; } }) }));
vi.mock('ant-design-vue', () => ({ message: { error: mocks.error, success: mocks.success } }));

const team = { id: 'team-1', unit_id: 'u1', project_id: 'p1', name: '北江联合研判', description: '防洪会商', enabled: false, draft_revision: 2, published_version: 1, member_count: 1, supervisor: { agent_id: 'forecast', role: 'supervisor', responsibility: '统筹', agent_definition_digest: 'a'.repeat(64) }, updated_at: '2026-09-01T00:00:00Z' };
const draft = { id: 'draft-1', team_id: 'team-1', version: 0, status: 'draft', definition: { supervisor: { agent_id: 'forecast', responsibility: '统筹', tool_ids: [], skill_names: [], knowledge_source_ids: [] }, members: [{ agent_id: 'review', responsibility: '复核', tool_ids: [], skill_names: [], knowledge_source_ids: [] }], tool_ids: [], skill_names: [], knowledge_source_ids: [], max_steps: 8, max_parallel_members: 1, timeout_seconds: 600, failure_strategy: 'fail_fast', approval_policy_id: null }, definition_digest: null, members: [], max_steps: 8, max_parallel_members: 1, timeout_seconds: 600, failure_strategy: 'fail_fast', published_by: null, published_at: null };
const published = { ...draft, id: 'version-1', version: 1, status: 'published', definition_digest: 'b'.repeat(64), published_by: 'admin', published_at: '2026-09-01T00:00:00Z' };
const stubs = {
  'a-button': { props: ['disabled'], emits: ['click'], template: '<button v-bind="$attrs" :disabled="disabled" @click="$emit(\'click\')"><slot name="icon"/><slot/></button>' },
  'a-input': { template: '<input v-bind="$attrs" />' }, 'a-input-number': { template: '<input type="number" v-bind="$attrs" />' },
  'a-select': { props: ['options', 'value'], template: '<select><slot/></select>' }, 'a-tag': { template: '<span><slot/></span>' },
  'a-switch': { emits: ['change'], template: '<button class="switch" @click="$emit(\'change\', true)" />' },
  'a-drawer': { props: ['open'], template: '<aside v-if="open"><slot/><slot name="footer"/></aside>' },
  'a-modal': { props: ['open'], template: '<div v-if="open"><slot/></div>' }, 'a-empty': { template: '<div><slot/></div>' },
  'a-spin': { template: '<div><slot/></div>' }, 'a-alert': { props: ['message', 'description'], template: '<div>{{message}}{{description}}<slot/></div>' },
};

function render() { return mount(TeamManageView, { global: { stubs } }); }

beforeEach(() => {
  vi.clearAllMocks(); mocks.canManage = true; mocks.list.mockResolvedValue([team]); mocks.agents.mockResolvedValue([
    { id: 'forecast', name: '洪水预报智能体', enabled: true, startup_status: 'ready' },
    { id: 'review', name: '成果复核智能体', enabled: true, startup_status: 'ready' },
  ]); mocks.getVersion.mockResolvedValue(draft); mocks.listVersions.mockResolvedValue([published]);
  mocks.saveDraft.mockResolvedValue(team); mocks.publish.mockResolvedValue(published); mocks.setEnabled.mockResolvedValue(team);
});

describe('TeamManageView', () => {
  it('loads real teams and agents, then saves and publishes the selected draft', async () => {
    const wrapper = render(); await flushPromises();
    expect(wrapper.text()).toContain('北江联合研判');
    await wrapper.get('[data-testid="team-edit"]').trigger('click'); await flushPromises();
    expect(mocks.getVersion).toHaveBeenCalledWith('team-1', 0);
    expect(wrapper.text()).toContain('洪水预报智能体'); expect(wrapper.text()).toContain('成果复核智能体');
    expect(wrapper.text()).toContain('版本 1');
    await wrapper.get('[data-testid="team-publish"]').trigger('click'); await flushPromises();
    expect(mocks.saveDraft).toHaveBeenCalledWith('team-1', 2, draft.definition);
    expect(mocks.publish).toHaveBeenCalledWith('team-1');
  });

  it('shows field-specific draft validation and does not publish an invalid definition', async () => {
    mocks.getVersion.mockResolvedValue({ ...draft, definition: { ...draft.definition, supervisor: { ...draft.definition.supervisor, agent_id: '' }, members: [] } });
    const wrapper = render(); await flushPromises(); await wrapper.get('[data-testid="team-edit"]').trigger('click'); await flushPromises();
    await wrapper.get('[data-testid="team-publish"]').trigger('click');
    expect(wrapper.text()).toContain('请选择主管智能体'); expect(wrapper.text()).toContain('至少选择一个成员智能体');
    expect(mocks.publish).not.toHaveBeenCalled();
  });

  it('keeps mutation controls hidden for readers while retaining read-only history', async () => {
    mocks.canManage = false;
    const wrapper = render(); await flushPromises();
    expect(wrapper.find('[data-testid="team-create"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="team-edit"]').exists()).toBe(false);
    await wrapper.get('[data-testid="team-history"]').trigger('click'); await flushPromises();
    expect(wrapper.text()).toContain('版本 1');
    expect(wrapper.find('[data-testid="team-publish"]').exists()).toBe(false);
  });
});
