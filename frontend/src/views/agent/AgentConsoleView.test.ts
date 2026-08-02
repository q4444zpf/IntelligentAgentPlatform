import { describe, expect, it } from 'vitest';

import source from './AgentConsoleView.vue?raw';

describe('AgentConsoleView runtime contract', () => {
  it('keeps the water console while using persisted conversation runs', () => {
    expect(source).toContain('class="conversation-page"');
    expect(source).toContain('useConversationStore');
    expect(source).toContain('runtimeStatusLabel');
  });

  it('does not simulate replies or claim an isolated sandbox before execution', () => {
    expect(source).not.toContain('setTimeout');
    expect(source).not.toContain('initialMessages');
    expect(source).not.toContain('沙箱已隔离');
    expect(source).toContain('isRunActive(conversationStore.activeRun?.status)');
  });

  it('defaults to the available single-agent runtime', () => {
    expect(source).toContain("const mode = ref<ChatMode>('single')");
    expect(source).toContain('title="多智能体运行时开发中" disabled');
  });

  it('loads enabled web and common agents and follows the platform default', () => {
    expect(source).toContain('agentsApi.list');
    expect(source).toContain('agent.is_default');
    expect(source).toContain("agent.runtime_form === 'web' || agent.runtime_form === 'common'");
    expect(source).not.toContain("ref('flood')");
  });

  it('preserves explicit selection and permits backend default resolution', () => {
    expect(source).toContain('hasExplicitAgentSelection');
    expect(source).toContain('selectedAgentId.value || undefined');
  });
});