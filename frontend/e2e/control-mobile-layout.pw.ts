import { expect, test } from '@playwright/test';

test('renders control workflow without horizontal overflow or section overlap at 390px', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route('**/api/auth/me', route => route.fulfill({ json: {
    user: { id: 'operator-1', display_name: '操作员' }, unit_id: 'unit-1', current_project_id: 'project-1',
    current_project: { id: 'project-1', name: '测试项目' }, projects: [{ id: 'project-1', name: '测试项目' }],
    auth_method: 'dev_test', authorization_version: 1, roles: ['unit_admin'],
    permissions: [{ code: 'control.read', target: 'current_project' }, { code: 'control.manage', target: 'current_project' }, { code: 'control.approve', target: 'current_project' }],
    menus: [], csrf_token: 'test-token',
    session: { idle_expires_at: '2099-01-01T00:00:00Z', absolute_expires_at: '2099-01-02T00:00:00Z' },
  } }));
  await page.route('**/api/control/policies*', route => route.fulfill({ json: [{
    id: 'policy-1', unit_id: 'unit-1', project_id: 'project-1', owner_id: 'operator-1', revision: 1,
    asset_type: 'gate', command_type: 'open', parameter_schema: {}, max_command_lifetime_seconds: 600,
    approval_mode: 'dual', deviation_thresholds: {}, enabled: true,
    created_at: '2026-09-15T10:00:00Z', updated_at: '2026-09-15T10:00:00Z',
  }] }));
  await page.route('**/api/control/commands*', route => route.fulfill({ json: [{
    id: 'command-1', unit_id: 'unit-1', project_id: 'project-1', policy_id: 'policy-1', requester_id: 'operator-1',
    run_id: null, team_version_id: null, policy_digest: 'policy-digest', policy_snapshot: {}, deviation_thresholds_snapshot: {},
    feedback_observed_at: null, target: { asset_type: 'gate', id: 'gate-1' }, parameters: { opening: 20 },
    parameter_digest: 'parameter-digest', simulation_artifact_id: 'simulation-1', simulation_digest: 'simulation-digest',
    valid_from: '2026-09-15T12:00:00Z', valid_until: '2026-09-15T12:10:00Z', required_approvals: 2, approval_count: 1,
    rollback_plan: { opening: 0 }, expected_state: { opening: 20 }, actual_state: null,
    deviation_result: null, external_command_ref: null, idempotency_key: 'proposal-1', status: 'pending_approval',
  }] }));

  await page.goto('/control');
  await expect(page.locator('.command-row')).toHaveCount(1);
  await expect(page.locator('.policy-row')).toHaveCount(1);

  const bounds = await page.evaluate(() => {
    const rect = (selector: string) => {
      const element = document.querySelector(selector);
      if (!element) throw new Error(`Missing ${selector}`);
      const { left, right, top, bottom } = element.getBoundingClientRect();
      return { left, right, top, bottom };
    };
    return {
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      heading: rect('.page-heading'),
      commands: rect('.control-command-list'),
      policy: rect('.control-policy-panel'),
    };
  });
  expect(bounds.scrollWidth).toBeLessThanOrEqual(bounds.clientWidth);
  expect(bounds.heading.bottom).toBeLessThanOrEqual(bounds.commands.top);
  expect(bounds.commands.bottom).toBeLessThanOrEqual(bounds.policy.top);
  for (const section of [bounds.heading, bounds.commands, bounds.policy]) {
    expect(section.left).toBeGreaterThanOrEqual(0);
    expect(section.right).toBeLessThanOrEqual(bounds.clientWidth);
  }
});
