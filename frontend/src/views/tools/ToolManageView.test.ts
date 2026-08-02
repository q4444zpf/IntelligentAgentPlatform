import { describe, expect, it } from 'vitest';

import apiSource from '@/api/tools.ts?raw';
import routesSource from '@/router/routes.ts?raw';
import permissionSource from '@/stores/permission.ts?raw';
import source from './ToolManageView.vue?raw';

describe('ToolManageView contract', () => {
  it('provides registry and invocation API operations', () => {
    expect(apiSource).toContain('list:');
    expect(apiSource).toContain('get:');
    expect(apiSource).toContain('toggle:');
    expect(apiSource).toContain('listInvocations:');
    expect(apiSource).toContain('/agent-runs/${encodeURIComponent(runId)}/tool-invocations');
  });

  it('shows registered built-ins, risks and schema summaries', () => {
    expect(source).toContain('toolsApi.list');
    expect(source).toContain('系统内置');
    expect(source).toContain('riskLabel');
    expect(source).toContain('Schema');
    expect(source).toContain('toolsApi.toggle');
    expect(source).not.toContain('DeleteOutlined');
  });

  it('provides filtering and visible load errors', () => {
    expect(source).toContain('filteredTools');
    expect(source).toContain('sourceFilter');
    expect(source).toContain('riskFilter');
    expect(source).toContain('loadError');
    expect(source).toContain('切换状态失败');
  });

  it('registers a separately permitted tools route and menu', () => {
    expect(routesSource).toContain("path: '/tools'");
    expect(routesSource).toContain("path: 'tools'");
    expect(routesSource).toContain("permission: 'tool:view'");
    expect(routesSource).toContain("title: 'Skill 管理'");
    expect(routesSource).toContain("title: '工具注册中心'");
    expect(permissionSource).toContain("'tool:view'");
  });
});