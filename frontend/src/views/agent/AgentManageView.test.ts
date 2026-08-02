import { describe, expect, it } from 'vitest';

import apiSource from '@/api/agents.ts?raw';
import source from './AgentManageView.vue?raw';

describe('AgentManageView default-agent contract', () => {
  it('exposes default-agent flags and management endpoints', () => {
    expect(apiSource).toContain('is_builtin: boolean');
    expect(apiSource).toContain('is_default: boolean');
    expect(apiSource).toContain('getDefault:');
    expect(apiSource).toContain('setDefault:');
  });

  it('renders built-in and platform-default state', () => {
    expect(source).toContain('平台默认');
    expect(source).toContain('系统内置');
    expect(source).toContain('agent.is_default');
    expect(source).toContain('agent.is_builtin');
  });

  it('switches the default through the management API', () => {
    expect(source).toContain('agentsApi.setDefault');
    expect(source).toContain('平台默认智能体已更新');
  });

  it('protects default and built-in agents from invalid actions', () => {
    expect(source).toContain('agent.is_builtin || agent.is_default');
    expect(source).toContain(':disabled="agent.is_default"');
    expect(source).toContain('平台默认智能体不能停用或删除');
    expect(source).toContain('系统内置智能体不能删除');
  });

  it('loads tools with the other editor dependencies and submits tool bindings', () => {
    expect(apiSource).toContain('tool_ids: string[]');
    expect(source).toContain('toolsApi.list()');
    expect(source).toContain('toolData');
    expect(source).toContain('tool_ids: [] as string[]');
    expect(source).toContain('const { id, ...payload } = form');
  });

  it('offers only published enabled tools while retaining unavailable bindings', () => {
    expect(source).toContain('授权工具');
    expect(source).toContain('tool.published && tool.enabled');
    expect(source).toContain('form.tool_ids.includes(tool.tool_id)');
    expect(source).toContain('已停用，仅保留现有绑定');
    expect(source).toContain('tool.risk_level');
    expect(source).toContain('tool.source');
  });
});