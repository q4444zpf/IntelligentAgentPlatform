// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AgentManageView from './AgentManageView.vue';
import apiSource from '@/api/agents.ts?raw';
import source from './AgentManageView.vue?raw';

const mocks = vi.hoisted(() => ({
  agentsList: vi.fn(), providersList: vi.fn(), skillsList: vi.fn(), toolsList: vi.fn(),
  update: vi.fn(), create: vi.fn(), copy: vi.fn(), showError: vi.fn(), showSuccess: vi.fn(),
}));
vi.mock('@/api/agents', () => ({ agentsApi: { list: mocks.agentsList, update: mocks.update, create: mocks.create, copy: mocks.copy, setDefault: vi.fn(), toggle: vi.fn(), pin: vi.fn(), remove: vi.fn() } }));
vi.mock('@/api/modelProviders', () => ({ modelProviderApi: { list: mocks.providersList } }));
vi.mock('@/api/skills', () => ({ skillsApi: { list: mocks.skillsList } }));
vi.mock('@/api/tools', () => ({ toolsApi: { list: mocks.toolsList } }));
vi.mock('ant-design-vue', () => ({ message: { error: mocks.showError, success: mocks.showSuccess } }));

const agent = { id: 'default-agent', name: '默认智能体', description: '', runtime_form: 'common', language: 'zh-CN', provider_id: '', model: '', system_prompt: '', context_prompt: '', approval_policy: 'never', skill_names: [], tool_ids: ['disabled.tool'], enabled: true, is_builtin: true, is_default: true, pinned: false, startup_status: 'ready', workspace_dir: 'agents/default', created_at: '2026-08-02T00:00:00Z', updated_at: '2026-08-02T00:00:00Z' };
const disabledTool = { tool_id: 'disabled.tool', version: '1.0.0', name: '停用工具', description: '', source: 'builtin', risk_level: 'low', input_schema: {}, output_schema: {}, requires_approval: false, published: true, enabled: false, is_builtin: true, created_at: '', updated_at: '' };
const stubs = {
  'a-alert': { props: ['description', 'message'], template: '<div class="alert"><slot />{{ message }}{{ description }}<slot name="action" /></div>' },
  'a-space': { template: '<div><slot /></div>' },
  'a-input': { template: '<input />' }, 'a-textarea': { template: '<textarea />' }, 'a-select': { template: '<div />' }, 'a-segmented': { template: '<div />' },
  'a-button': { props: ['disabled'], emits: ['click'], template: '<button v-bind="$attrs" :disabled="disabled" @click="$emit(\'click\', $event)"><slot name="icon" /><slot /></button>' },
  'a-switch': { template: '<button />' }, 'a-spin': { template: '<div><slot /></div>' }, 'a-tag': { template: '<span><slot /></span>' }, 'a-empty': { template: '<div><slot /></div>' },
  'a-tooltip': { template: '<div><slot /></div>' }, 'a-popconfirm': { template: '<div><slot /></div>' }, 'a-form': { template: '<form><slot /></form>' }, 'a-form-item': { template: '<div><slot /></div>' },
  'a-tabs': { template: '<div><slot /></div>' }, 'a-tab-pane': { template: '<section><slot /></section>' }, 'a-radio-group': { template: '<div><slot /></div>' }, 'a-radio': { template: '<label><slot /></label>' }, 'a-checkbox': { template: '<input type="checkbox" />' },
  'a-modal': { props: ['open'], emits: ['ok'], template: '<div v-if="open" class="modal"><slot /><button class="modal-ok" @click="$emit(\'ok\')">保存</button></div>' },
};
const wrappers: ReturnType<typeof mount>[] = [];
function render() { const wrapper = mount(AgentManageView, { global: { stubs } }); wrappers.push(wrapper); return wrapper; }

beforeEach(() => { Object.values(mocks).forEach((mock) => mock.mockReset()); mocks.agentsList.mockResolvedValue([structuredClone(agent)]); mocks.providersList.mockResolvedValue([]); mocks.skillsList.mockResolvedValue([]); mocks.toolsList.mockResolvedValue([structuredClone(disabledTool)]); mocks.update.mockResolvedValue(structuredClone(agent)); });
afterEach(() => wrappers.splice(0).forEach((wrapper) => wrapper.unmount()));

describe('AgentManageView tool interactions', () => {
  it('shows a disabled binding, blocks save and copy, then removes it from update payload', async () => {
    const wrapper = render(); await flushPromises();
    expect(wrapper.get('[aria-label="复制智能体"]').attributes('disabled')).toBeDefined();
    await wrapper.get('[aria-label="复制智能体"]').trigger('click'); expect(mocks.copy).not.toHaveBeenCalled();
    await wrapper.get('[aria-label="编辑智能体"]').trigger('click'); await flushPromises();
    expect(wrapper.text()).toContain('停用工具'); expect(wrapper.text()).toContain('已停用或未发布，仅保留现有绑定');
    await wrapper.get('.modal-ok').trigger('click'); expect(mocks.update).not.toHaveBeenCalled(); expect(mocks.showError).toHaveBeenCalledWith('请先移除不可用的工具绑定再保存');
    await wrapper.get('.tool-remove').trigger('click'); await wrapper.get('.modal-ok').trigger('click'); await flushPromises();
    expect(mocks.update).toHaveBeenCalledWith('default-agent', expect.objectContaining({ tool_ids: [] }));
  });

  it('keeps core data usable when tool loading fails and retries locally', async () => {
    mocks.toolsList.mockRejectedValueOnce(new Error('工具目录不可用')).mockResolvedValueOnce([]);
    const wrapper = render(); await flushPromises();
    expect(wrapper.text()).toContain('默认智能体');
    await wrapper.get('[aria-label="编辑智能体"]').trigger('click'); await flushPromises();
    expect(wrapper.text()).toContain('工具目录不可用');
    await wrapper.get('.tool-retry').trigger('click'); await flushPromises();
    expect(mocks.toolsList).toHaveBeenCalledTimes(2); expect(wrapper.text()).not.toContain('工具目录不可用');
  });
});

describe('AgentManageView contracts', () => {
  it('keeps default protection and tool contracts explicit', () => {
    expect(apiSource).toContain('tool_ids: string[]'); expect(source).toContain('agentsApi.setDefault');
    expect(source).toContain('agent.is_builtin || agent.is_default'); expect(source).toContain('toolsApi.list');
  });
});