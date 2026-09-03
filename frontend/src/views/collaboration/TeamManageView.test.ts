// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import TeamManageView from './TeamManageView.vue';

const mocks = vi.hoisted(() => ({
  list: vi.fn(), getVersion: vi.fn(), listVersions: vi.fn(), saveDraft: vi.fn(), publish: vi.fn(),
  create: vi.fn(), update: vi.fn(), setEnabled: vi.fn(), agents: vi.fn(), tools: vi.fn(), skills: vi.fn(), error: vi.fn(), success: vi.fn(),
  canManage: true,
}));
vi.mock('@/api/teams', () => ({ teamsApi: {
  list: mocks.list, getVersion: mocks.getVersion, listVersions: mocks.listVersions,
  saveDraft: mocks.saveDraft, publish: mocks.publish, create: mocks.create,
  update: mocks.update, setEnabled: mocks.setEnabled,
} }));
vi.mock('@/api/agents', () => ({ agentsApi: { list: mocks.agents } }));
vi.mock('@/api/tools', () => ({ toolsApi: { list: mocks.tools } }));
vi.mock('@/api/skills', () => ({ skillsApi: { list: mocks.skills } }));
vi.mock('@/stores/permission', () => ({ usePermissionStore: () => ({ hasPermission: (permission: string) => mocks.canManage && permission.startsWith('collaboration.manage:') }) }));
vi.mock('ant-design-vue', () => ({ message: { error: mocks.error, success: mocks.success } }));

const team = { id: 'team-1', unit_id: 'u1', project_id: 'p1', name: '北江联合研判', description: '防洪会商', enabled: false, draft_revision: 2, published_version: 1, member_count: 1, supervisor: { agent_id: 'forecast', role: 'supervisor', responsibility: '统筹', agent_definition_digest: 'a'.repeat(64) }, updated_at: '2026-09-01T00:00:00Z' };
const draft = { id: 'draft-1', team_id: 'team-1', version: 0, status: 'draft', definition: { supervisor: { agent_id: 'forecast', responsibility: '统筹', tool_ids: [], skill_names: [], knowledge_source_ids: [] }, members: [{ agent_id: 'review', responsibility: '复核', tool_ids: [], skill_names: [], knowledge_source_ids: [] }], tool_ids: [], skill_names: [], knowledge_source_ids: [], max_steps: 8, max_parallel_members: 1, timeout_seconds: 600, failure_strategy: 'fail_fast', approval_policy_id: null }, definition_digest: null, members: [], max_steps: 8, max_parallel_members: 1, timeout_seconds: 600, failure_strategy: 'fail_fast', published_by: null, published_at: null };
const published = { ...draft, id: 'version-1', version: 1, status: 'published', definition_digest: 'b'.repeat(64), published_by: 'admin', published_at: '2026-09-01T00:00:00Z' };
const stubs = {
  'a-button': { props: ['disabled'], emits: ['click'], template: '<button v-bind="$attrs" :disabled="disabled" @click="$emit(\'click\')"><slot name="icon"/><slot/></button>' },
  'a-input': { template: '<input v-bind="$attrs" />' }, 'a-input-number': { template: '<input type="number" v-bind="$attrs" />' },
  'a-select': { props: ['mode', 'options', 'value'], emits: ['update:value'], template: '<select v-bind="$attrs" :value="value" @change="$emit(\'update:value\', mode === \'multiple\' ? [$event.target.value] : $event.target.value)"><option v-for="option in options" :key="option.value" :value="option.value">{{ option.label }}</option><slot/></select>' }, 'a-tag': { template: '<span><slot/></span>' },
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
  mocks.tools.mockResolvedValue([{ tool_id: 'forecast.read', name: '预报查询', published: true, enabled: true, source_available: true, source: 'builtin' }]);
  mocks.skills.mockResolvedValue([{ name: 'forecast', description: '预报技能', enabled: true }]);
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

  it('opens a newly created empty server draft as a valid editable form', async () => {
    mocks.getVersion.mockResolvedValue({ ...draft, definition: {} });
    const wrapper = render(); await flushPromises();

    await wrapper.get('[data-testid="team-edit"]').trigger('click');
    await flushPromises();

    expect(wrapper.text()).toContain('成员与职责');
    expect(wrapper.text()).toContain('运行边界');
    await wrapper.get('[data-testid="team-publish"]').trigger('click');
    expect(wrapper.text()).toContain('请选择主管智能体');
    expect(wrapper.text()).toContain('至少选择一个成员智能体');
    expect(mocks.saveDraft).not.toHaveBeenCalled();
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

  it('allows a project collaboration manager to edit Team and member capability controls', async () => {
    const wrapper = render(); await flushPromises();
    expect(wrapper.find('[data-testid="team-create"]').exists()).toBe(true);
    await wrapper.get('[data-testid="team-edit"]').trigger('click'); await flushPromises();
    expect(wrapper.text()).toContain('团队工具白名单');
    expect(wrapper.text()).toContain('成员工具白名单');
    expect(wrapper.text()).toContain('团队 Skill 白名单');
    expect(wrapper.text()).toContain('成员知识库白名单');
    expect(wrapper.text()).toContain('人工确认策略');
  });

  it('reports field-level capability validation before sending an invalid draft', async () => {
    mocks.getVersion.mockResolvedValue({ ...draft, definition: {
      ...draft.definition,
      tool_ids: ['unbound.tool'],
      supervisor: { ...draft.definition.supervisor, tool_ids: ['unbound.tool'] },
    } });
    const wrapper = render(); await flushPromises(); await wrapper.get('[data-testid="team-edit"]').trigger('click'); await flushPromises();
    await wrapper.get('[data-testid="team-publish"]').trigger('click');
    expect(wrapper.text()).toContain('团队工具白名单包含不可用或未绑定工具');
    expect(mocks.saveDraft).not.toHaveBeenCalled();
  });

  it('assigns supervisor capabilities and saves them with the draft', async () => {
    mocks.agents.mockResolvedValue([
      { id: 'forecast', name: '洪水预报智能体', enabled: true, startup_status: 'ready', tool_ids: ['forecast.read'], skill_names: ['forecast'], knowledge_source_ids: ['knowledge.rainfall'] },
      { id: 'review', name: '成果复核智能体', enabled: true, startup_status: 'ready', tool_ids: [], skill_names: [], knowledge_source_ids: [] },
    ]);
    mocks.tools.mockResolvedValue([
      { tool_id: 'forecast.read', name: '预报查询', published: true, enabled: true, source_available: true, source: 'builtin' },
      { tool_id: 'knowledge.rainfall', name: '降雨知识库', published: true, enabled: true, source_available: true, source: 'knowledge' },
    ]);
    mocks.getVersion.mockResolvedValue({ ...draft, definition: {
      ...draft.definition,
      tool_ids: ['forecast.read'], skill_names: ['forecast'], knowledge_source_ids: ['knowledge.rainfall'],
    } });

    const wrapper = render(); await flushPromises(); await wrapper.get('[data-testid="team-edit"]').trigger('click'); await flushPromises();
    await wrapper.get('[data-testid="supervisor-tool-whitelist"]').setValue('forecast.read');
    await wrapper.get('[data-testid="team-publish"]').trigger('click'); await flushPromises();

    expect(mocks.saveDraft).toHaveBeenCalledWith('team-1', 2, expect.objectContaining({
      supervisor: expect.objectContaining({ tool_ids: ['forecast.read'] }),
    }));
  });
});
