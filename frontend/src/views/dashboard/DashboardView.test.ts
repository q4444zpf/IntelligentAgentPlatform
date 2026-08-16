import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const source = readFileSync(fileURLToPath(new URL('./DashboardView.vue', import.meta.url)), 'utf8');

describe('DashboardView service status panel', () => {
  it('shows the compact service controls and uses five-minute polling', () => {
    expect(source).toContain('基础服务状态');
    expect(source).toContain('刷新服务状态');
    expect(source).toContain('300000');
    expect(source).not.toContain('API 正常 · v');
    expect(source).not.toContain('>刷新状态</a-button>');
  });
});
