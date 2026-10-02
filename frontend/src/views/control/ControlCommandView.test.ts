// @vitest-environment happy-dom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import { createPinia } from 'pinia';
import Antd from 'ant-design-vue';
import ControlCommandView from './ControlCommandView.vue';
import ControlCommandProposalModal from './components/ControlCommandProposalModal.vue';
import { controlApi, type ControlCommandDetail, type ControlCommandListItem, type ControlProposal } from '@/api/control';
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
    expect(document.body.textContent).not.toContain('not json');
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

  it('retains action identity through auth, network, server and in-progress failures until terminal conflict', async () => {
    vi.mocked(controlApi.listCommands).mockResolvedValue([{ ...command, id: 'identity-sequence', status: 'approved' }]);
    vi.mocked(controlApi.execute)
      .mockRejectedValueOnce(new ApiError('unauthenticated', 401))
      .mockRejectedValueOnce(new ApiError('forbidden', 403))
      .mockRejectedValueOnce(new ApiError('offline', 0))
      .mockRejectedValueOnce(new ApiError('unavailable', 503))
      .mockRejectedValueOnce(new ApiError('in progress', 409, 'control_execution_in_progress'))
      .mockRejectedValueOnce(new ApiError('terminal conflict', 409, 'control_invalid_state'))
      .mockResolvedValueOnce(command)
      .mockResolvedValueOnce(command);
    const wrapper = mountView(['control:view', 'control:execute']); await flushPromises();
    for (let attempt = 0; attempt < 8; attempt += 1) {
      await wrapper.find('[aria-label="执行命令"]').trigger('click'); await flushPromises();
      (document.body.querySelector('[aria-label="确认执行命令"]') as HTMLButtonElement).click(); await flushPromises();
    }
    const keys = vi.mocked(controlApi.execute).mock.calls.map(([, key]) => key);
    expect(new Set(keys.slice(0, 6)).size).toBe(1);
    expect(keys[6]).not.toBe(keys[5]);
    expect(keys[7]).not.toBe(keys[6]);
    wrapper.unmount();
  });

  it('retains proposal identity across uncertain retry and changes it for new material inputs', async () => {
    const draft: ControlProposal = {
      policy_id: 'policy-1', target: { id: 'gate-1' }, parameters: { opening: 20 },
      simulation_artifact_id: 'simulation-1', valid_from: '2026-09-15T12:00:00Z', valid_until: '2026-09-15T12:10:00Z',
      rollback_plan: {}, expected_state: { opening: 20 }, idempotency_key: 'modal-key-1',
    };
    vi.mocked(controlApi.propose)
      .mockRejectedValueOnce(new ApiError('offline', 0))
      .mockRejectedValueOnce(new ApiError('unauthenticated', 401))
      .mockRejectedValueOnce(new ApiError('offline', 0))
      .mockResolvedValueOnce(command)
      .mockResolvedValueOnce(command);
    const wrapper = mountView(['control:view', 'control:propose']); await flushPromises();
    const modal = wrapper.findComponent(ControlCommandProposalModal);
    modal.vm.$emit('submit', draft); await flushPromises();
    modal.vm.$emit('submit', { ...draft, idempotency_key: 'modal-key-2' }); await flushPromises();
    modal.vm.$emit('submit', { ...draft, parameters: { opening: 30 }, idempotency_key: 'modal-key-3' }); await flushPromises();
    modal.vm.$emit('submit', { ...draft, parameters: { opening: 30 }, idempotency_key: 'modal-key-4' }); await flushPromises();
    modal.vm.$emit('submit', { ...draft, parameters: { opening: 30 }, idempotency_key: 'modal-key-5' }); await flushPromises();
    const bodies = vi.mocked(controlApi.propose).mock.calls.map(([body]) => body);
    expect(bodies[0].idempotency_key).toBe(bodies[1].idempotency_key);
    expect(bodies[2].idempotency_key).not.toBe(bodies[1].idempotency_key);
    expect(bodies[2].idempotency_key).toBe(bodies[3].idempotency_key);
    expect(bodies[4].idempotency_key).not.toBe(bodies[3].idempotency_key);
    wrapper.unmount();
  });

  it('establishes a new proposal identity after a terminal idempotency conflict', async () => {
    const draft: ControlProposal = {
      policy_id: 'policy-1', target: { id: 'gate-1' }, parameters: { opening: 20 },
      simulation_artifact_id: 'simulation-1', valid_from: '2026-09-15T12:00:00Z', valid_until: '2026-09-15T12:10:00Z',
      rollback_plan: {}, expected_state: null, idempotency_key: 'modal-key-1',
    };
    vi.mocked(controlApi.propose).mockRejectedValueOnce(new ApiError('conflict', 409, 'control_idempotency_conflict')).mockResolvedValueOnce(command);
    const wrapper = mountView(['control:view', 'control:propose']); await flushPromises();
    const modal = wrapper.findComponent(ControlCommandProposalModal);
    modal.vm.$emit('submit', draft); await flushPromises();
    modal.vm.$emit('submit', { ...draft, idempotency_key: 'modal-key-2' }); await flushPromises();
    const keys = vi.mocked(controlApi.propose).mock.calls.map(([body]) => body.idempotency_key);
    expect(keys[1]).not.toBe(keys[0]);
    wrapper.unmount();
  });

  it('reuses proposal identity when JSON object keys are reordered without changing values', async () => {
    const draft: ControlProposal = {
      policy_id: 'policy-1', target: { asset_type: 'gate', id: 'gate-1' }, parameters: { opening: 20, duration: 30 },
      simulation_artifact_id: 'simulation-1', valid_from: '2026-09-15T12:00:00Z', valid_until: '2026-09-15T12:10:00Z',
      rollback_plan: {}, expected_state: null, idempotency_key: 'modal-1',
    };
    vi.mocked(controlApi.propose).mockRejectedValue(new ApiError('offline', 0));
    const wrapper = mountView(['control:view', 'control:propose']); await flushPromises();
    const modal = wrapper.findComponent(ControlCommandProposalModal);
    modal.vm.$emit('submit', draft); await flushPromises();
    modal.vm.$emit('submit', { ...draft, target: { id: 'gate-1', asset_type: 'gate' }, parameters: { duration: 30, opening: 20 }, idempotency_key: 'modal-2' }); await flushPromises();
    const keys = vi.mocked(controlApi.propose).mock.calls.map(([body]) => body.idempotency_key);
    expect(keys[0]).toBe(keys[1]);
    wrapper.unmount();
  });

  it('keeps an uncertain proposal key when the console is remounted', async () => {
    const draft: ControlProposal = {
      policy_id: 'policy-1', target: { id: 'gate-remount' }, parameters: { opening: 42 },
      simulation_artifact_id: 'simulation-remount', valid_from: '2026-09-15T12:00:00Z', valid_until: '2026-09-15T12:10:00Z',
      rollback_plan: {}, expected_state: null, idempotency_key: 'modal-1',
    };
    vi.mocked(controlApi.propose).mockRejectedValue(new ApiError('offline', 0));
    const first = mountView(['control:view', 'control:propose']); await flushPromises();
    first.findComponent(ControlCommandProposalModal).vm.$emit('submit', draft); await flushPromises();
    first.unmount();
    const second = mountView(['control:view', 'control:propose']); await flushPromises();
    second.findComponent(ControlCommandProposalModal).vm.$emit('submit', { ...draft, idempotency_key: 'modal-2' }); await flushPromises();
    const keys = vi.mocked(controlApi.propose).mock.calls.map(([body]) => body.idempotency_key);
    expect(keys[0]).toBe(keys[1]);
    second.unmount();
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

  it('renders command and policy sections for a narrow viewport', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 });
    const wrapper = mountView(); await flushPromises();
    expect(wrapper.find('.control-page').exists()).toBe(true);
    expect(wrapper.find('.control-command-list').exists()).toBe(true);
    expect(wrapper.find('.control-policy-panel').exists()).toBe(true);
    wrapper.unmount();
  });
});
