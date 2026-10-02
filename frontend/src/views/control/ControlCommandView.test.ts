// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import { createPinia } from 'pinia';
import Antd from 'ant-design-vue';
import ControlCommandView from './ControlCommandView.vue';
import { controlApi, type ControlCommandDetail, type ControlCommandListItem } from '@/api/control';
import { usePermissionStore } from '@/stores/permission';
import { ApiError } from '@/api/client';

vi.mock('@/api/control', () => ({ controlApi: {
  listCommands: vi.fn(), listPolicies: vi.fn(), getCommand: vi.fn(), getPolicy: vi.fn(),
  createPolicy: vi.fn(), updatePolicy: vi.fn(), propose: vi.fn(), approve: vi.fn(), reject: vi.fn(),
  execute: vi.fn(), feedback: vi.fn(), rollback: vi.fn(),
} }));

const command: ControlCommandListItem = {
  id: 'command-1', unit_id: 'unit-1', project_id: 'project-1', policy_id: 'policy-1', requester_id: 'requester-1',
  run_id: null, team_version_id: null, policy_digest: 'policy-digest', policy_snapshot: {}, deviation_thresholds_snapshot: {},
  feedback_observed_at: '2026-09-15T12:03:00Z', target: { asset_type: 'gate', id: 'gate-1' }, parameters: { opening: 20 },
  parameter_digest: 'parameter-digest', simulation_artifact_id: 'simulation-1', simulation_digest: 'simulation-digest',
  valid_from: '2026-09-15T12:00:00Z', valid_until: '2026-09-15T12:10:00Z', required_approvals: 2, approval_count: 1,
  rollback_plan: { opening: 0 }, expected_state: { opening: 20 }, actual_state: { opening: 18 },
  deviation_result: { opening: { deviation: 2 } }, external_command_ref: 'dispatch-123', idempotency_key: 'proposal-1', status: 'awaiting_feedback',
};
const detail: ControlCommandDetail = {
  ...command, approval_decisions: [{ approver_id: 'approver-1', decision: 'approve', reason: null, decided_at: '2026-09-15T12:01:00Z' }],
  attempts: [{ id: 'attempt-1', kind: 'execute', outcome: 'succeeded', external_id: 'dispatch-123', created_at: '2026-09-15T12:02:00Z', completed_at: '2026-09-15T12:02:30Z' }],
  feedback_records: [{ id: 'feedback-1', recorder_id: 'operator-1', observed_at: '2026-09-15T12:03:00Z', actual_state: { opening: 18 }, deviation_result: { opening: { deviation: 2 } } }],
  simulation_attestation: { digest: 'attested-digest' }, rollback_provenance: { source: 'approved-plan' },
};

function mountView(permissions: string[] = ['control:view']) {
  const pinia = createPinia();
  const wrapper = mount(ControlCommandView, { attachTo: document.body, global: { plugins: [pinia, Antd] } });
  const store = usePermissionStore(pinia);
  store.permissions = permissions;
  return wrapper;
}

describe('ControlCommandView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(controlApi.listCommands).mockResolvedValue([command]);
    vi.mocked(controlApi.listPolicies).mockResolvedValue([]);
    vi.mocked(controlApi.getCommand).mockResolvedValue(detail);
    vi.mocked(controlApi.execute).mockResolvedValue(command);
    vi.mocked(controlApi.rollback).mockResolvedValue(command);
  });

  it('shows simulation approval execution feedback deviation and rollback detail', async () => {
    const wrapper = mountView(); await flushPromises();
    await wrapper.find('[aria-label="查看命令详情"]').trigger('click'); await flushPromises();
    expect(document.body.textContent).toContain('simulation-1');
    expect(document.body.textContent).toContain('attested-digest');
    expect(document.body.textContent).toContain('1 / 2');
    expect(document.body.textContent).toContain('dispatch-123');
    expect(document.body.textContent).toContain('attempt-1');
    expect(document.body.textContent).toContain('最新尝试');
    expect(document.body.textContent).toContain('策略快照');
    expect(document.body.textContent).toContain('feedback-1');
    expect(document.body.textContent).toContain('approved-plan');
    wrapper.unmount();
  });

  it('shows policy and proposal controls only with manage and propose permissions', async () => {
    const viewer = mountView(); await flushPromises();
    expect(viewer.find('[aria-label="新建策略"]').exists()).toBe(false);
    expect(viewer.find('[aria-label="新建控制申请"]').exists()).toBe(false);
    viewer.unmount();
    const manager = mountView(['control:view', 'control:manage', 'control:propose']); await flushPromises();
    expect(manager.find('[aria-label="新建策略"]').exists()).toBe(true);
    expect(manager.find('[aria-label="新建控制申请"]').exists()).toBe(true);
    manager.unmount();
  });

  it('keeps approval, execution and feedback actions absent without their own permission', async () => {
    vi.mocked(controlApi.listCommands).mockResolvedValue([
      { ...command, id: 'approval', status: 'pending_approval' },
      { ...command, id: 'execution', status: 'approved' },
      { ...command, id: 'feedback', status: 'awaiting_feedback' },
      { ...command, id: 'rollback', status: 'deviation' },
    ]);
    const viewer = mountView(['control:view']); await flushPromises();
    for (const label of ['批准命令', '拒绝命令', '执行命令', '记录反馈', '回滚命令']) {
      expect(viewer.find(`[aria-label="${label}"]`).exists()).toBe(false);
    }
    viewer.unmount();
  });

  it('shows feedback only in awaiting_feedback and abnormal reconciliation states', async () => {
    const wrapper = mountView(['control:view', 'control:feedback']); await flushPromises();
    expect(wrapper.find('[aria-label="记录反馈"]').exists()).toBe(true);
    vi.mocked(controlApi.listCommands).mockResolvedValue([{ ...command, status: 'approved' }]);
    await wrapper.find('[aria-label="刷新控制命令"]').trigger('click'); await flushPromises();
    expect(wrapper.find('[aria-label="记录反馈"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it('keeps invalid feedback in the form without sending a request', async () => {
    const wrapper = mountView(['control:view', 'control:feedback']); await flushPromises();
    await wrapper.find('[aria-label="记录反馈"]').trigger('click'); await flushPromises();
    const textarea = document.body.querySelector('.ant-modal textarea') as HTMLTextAreaElement;
    textarea.value = 'not json'; textarea.dispatchEvent(new Event('input', { bubbles: true })); await flushPromises();
    const submit = Array.from(document.body.querySelectorAll('.ant-modal-footer button')).find(button => button.textContent?.includes('提交反馈')) as HTMLButtonElement;
    submit.click(); await flushPromises();
    expect(document.body.textContent).toContain('JSON 必须为对象');
    expect(controlApi.feedback).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it('confirms execute and rollback and reuses the action idempotency key after timeout', async () => {
    vi.mocked(controlApi.listCommands).mockResolvedValue([{ ...command, status: 'approved' }]);
    vi.mocked(controlApi.execute).mockRejectedValueOnce(new ApiError('请求超时', 408)).mockResolvedValueOnce(command);
    const wrapper = mountView(['control:view', 'control:execute']); await flushPromises();
    await wrapper.find('[aria-label="执行命令"]').trigger('click'); await flushPromises();
    expect(controlApi.execute).not.toHaveBeenCalled();
    (document.body.querySelector('[aria-label="确认执行命令"]') as HTMLButtonElement).click(); await flushPromises();
    await wrapper.find('[aria-label="执行命令"]').trigger('click'); await flushPromises();
    (document.body.querySelector('[aria-label="确认执行命令"]') as HTMLButtonElement).click(); await flushPromises();
    expect(vi.mocked(controlApi.execute).mock.calls[0][1]).toBe(vi.mocked(controlApi.execute).mock.calls[1][1]);
    wrapper.unmount();
    vi.mocked(controlApi.listCommands).mockResolvedValue([{ ...command, status: 'deviation' }]);
    vi.mocked(controlApi.rollback).mockRejectedValueOnce(new ApiError('无法连接', 0)).mockResolvedValueOnce(command);
    const rollbackView = mountView(['control:view', 'control:execute']); await flushPromises();
    await rollbackView.find('[aria-label="回滚命令"]').trigger('click'); await flushPromises();
    expect(controlApi.rollback).not.toHaveBeenCalled();
    (document.body.querySelector('[aria-label="确认物理回滚命令"]') as HTMLButtonElement).click(); await flushPromises();
    await rollbackView.find('[aria-label="回滚命令"]').trigger('click'); await flushPromises();
    (document.body.querySelector('[aria-label="确认物理回滚命令"]') as HTMLButtonElement).click(); await flushPromises();
    expect(vi.mocked(controlApi.rollback).mock.calls[0][1]).toBe(vi.mocked(controlApi.rollback).mock.calls[1][1]);
    rollbackView.unmount();
  });

  it('renders stable safe error messages and preserves current list data on action failure', async () => {
    vi.mocked(controlApi.listCommands).mockResolvedValue([{ ...command, status: 'pending_approval' }]);
    vi.mocked(controlApi.approve).mockRejectedValue(new ApiError('Control request is invalid', 422, 'control_validation_failed'));
    const wrapper = mountView(['control:view', 'control:approve']); await flushPromises();
    await wrapper.find('[aria-label="批准命令"]').trigger('click'); await flushPromises();
    expect(wrapper.text()).toContain('command-1');
    expect(wrapper.text()).toContain('Control request is invalid');
    wrapper.unmount();
  });

  it('does not expose unexpected error text to operators', async () => {
    vi.mocked(controlApi.listCommands).mockRejectedValue(new Error('internal connection string: secret'));
    const wrapper = mountView(); await flushPromises();
    expect(wrapper.text()).toContain('操作失败，请重试');
    expect(wrapper.text()).not.toContain('internal connection string');
    wrapper.unmount();
  });

  it('renders a single-column non-overlapping workflow at 390px width', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 });
    const wrapper = mountView(); await flushPromises();
    expect(wrapper.find('.control-page').exists()).toBe(true);
    expect(wrapper.find('.control-command-list').exists()).toBe(true);
    expect(wrapper.find('.control-policy-panel').exists()).toBe(true);
    wrapper.unmount();
  });
});
