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
  });
});
