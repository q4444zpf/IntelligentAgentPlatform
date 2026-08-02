// @vitest-environment happy-dom
import { mount } from '@vue/test-utils';
import { reactive } from 'vue';
import { describe, expect, it, vi } from 'vitest';
import AgentConsoleView from './AgentConsoleView.vue';
import source from './AgentConsoleView.vue?raw';

const store = reactive({ conversations: [], activeConversationId: 'c1', messages: [], activeRun: { id: 'r1', status: 'running' }, error: '', sending: false, toolActivities: [
  { invocation_id: 'i1', display_name: '获取当前时间', tool_id: 'system.time', status: 'running', duration_ms: null, sequence: 1 },
  { invocation_id: 'i2', display_name: '运行上下文', tool_id: 'system.context', status: 'completed', duration_ms: 12, sequence: 2 },
  { invocation_id: 'i3', display_name: '失败工具', tool_id: 'system.fail', status: 'failed', duration_ms: 7, sequence: 3 },
], secret: 'SECRET_SENTINEL', loadConversations: vi.fn(), selectConversation: vi.fn(), startNewConversation: vi.fn(), sendMessage: vi.fn() });
vi.mock('@/stores/conversations', () => ({ useConversationStore: () => store }));
vi.mock('@/api/agents', () => ({ agentsApi: { list: vi.fn().mockResolvedValue([]) } }));
vi.mock('vue-router', () => ({ useRoute: () => ({ meta: {} }), useRouter: () => ({ push: vi.fn() }) }));

const stubs = { 'a-select': { template: '<div />' } };

describe('AgentConsoleView runtime interactions', () => {
  it('renders safe compact tool status and duration without unrelated store secrets', () => {
    const wrapper = mount(AgentConsoleView, { global: { stubs } });
    expect(wrapper.text()).toContain('获取当前时间'); expect(wrapper.text()).toContain('执行中');
    expect(wrapper.text()).toContain('运行上下文'); expect(wrapper.text()).toContain('完成'); expect(wrapper.text()).toContain('12 ms');
    expect(wrapper.text()).toContain('失败工具'); expect(wrapper.text()).toContain('失败'); expect(wrapper.text()).toContain('7 ms');
    expect(wrapper.text()).not.toContain('SECRET_SENTINEL'); wrapper.unmount();
  });
});

describe('AgentConsoleView runtime contract', () => {
  it('keeps persisted conversation runs and backend default resolution', () => {
    expect(source).toContain('class="conversation-page"'); expect(source).toContain('useConversationStore');
    expect(source).toContain('hasExplicitAgentSelection'); expect(source).toContain('selectedAgentId.value || undefined');
    expect(source).not.toContain('arguments_summary'); expect(source).not.toContain('result_summary');
    expect(source).not.toContain('setTimeout'); expect(source).not.toContain('initialMessages'); expect(source).not.toContain('沙箱已隔离');
    expect(source).toContain("const mode = ref<ChatMode>('single')"); expect(source).toContain('title="多智能体运行时开发中" disabled');
    expect(source).toContain("agent.enabled && (agent.runtime_form === 'web' || agent.runtime_form === 'common')");
  });
});