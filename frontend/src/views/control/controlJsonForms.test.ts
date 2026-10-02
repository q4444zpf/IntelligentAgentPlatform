// @vitest-environment happy-dom
import { afterEach, describe, expect, it } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import Antd from 'ant-design-vue';
import ControlPolicyPanel from './components/ControlPolicyPanel.vue';
import ControlCommandProposalModal from './components/ControlCommandProposalModal.vue';
import type { ControlPolicy } from '@/api/control';

const policy: ControlPolicy = {
  id: 'policy-1', unit_id: 'unit-1', project_id: 'project-1', owner_id: 'owner-1', revision: 1,
  asset_type: 'gate', command_type: 'open', parameter_schema: {}, max_command_lifetime_seconds: 600,
  approval_mode: 'dual', deviation_thresholds: {}, enabled: true,
  created_at: '2026-09-15T10:00:00Z', updated_at: '2026-09-15T10:00:00Z',
};

function setField(label: string, value: string) {
  const item = Array.from(document.body.querySelectorAll('.ant-modal .ant-form-item'))
    .find(element => element.textContent?.includes(label));
  const field = item?.querySelector('input, textarea') as HTMLInputElement | HTMLTextAreaElement;
  expect(field, `missing field ${label}`).toBeTruthy();
  field.value = value;
  field.dispatchEvent(new Event('input', { bubbles: true }));
}

function submitModal() {
  const button = document.body.querySelector('.ant-modal-footer button.ant-btn-primary') as HTMLButtonElement;
  expect(button).toBeTruthy();
  button.click();
}

describe('control JSON forms', () => {
  afterEach(() => { document.body.innerHTML = ''; });

  it('shows a fixed safe validation error for malformed policy JSON', async () => {
    const wrapper = mount(ControlPolicyPanel, { attachTo: document.body, props: { policies: [], canManage: true, loading: false, saving: false, error: '' }, global: { plugins: [Antd] } });
    await wrapper.find('[aria-label="新建策略"]').trigger('click'); await flushPromises();
    setField('资产类型', 'gate'); setField('命令类型', 'open');
    setField('参数 Schema', '{secret-policy-token'); await flushPromises();
    submitModal(); await flushPromises();
    expect(document.body.textContent).toContain('JSON 必须为对象');
    expect(document.body.textContent).not.toContain('secret-policy-token');
    expect(wrapper.emitted('save')).toBeUndefined();
    wrapper.unmount();
  });

  it('shows a fixed safe validation error for malformed proposal JSON', async () => {
    const wrapper = mount(ControlCommandProposalModal, { attachTo: document.body, props: { open: true, policies: [policy], saving: false, error: '' }, global: { plugins: [Antd] } });
    await flushPromises();
    const select = document.body.querySelector('.ant-modal .ant-select-selector') as HTMLElement;
    select.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })); await flushPromises();
    const option = document.body.querySelector('.ant-select-item-option') as HTMLElement;
    expect(option).toBeTruthy();
    option.click(); await flushPromises();
    setField('目标（JSON）', '{secret-proposal-token');
    setField('仿真工件 ID', 'simulation-1');
    setField('生效时间', '2026-09-15T12:00');
    setField('失效时间', '2026-09-15T12:10'); await flushPromises();
    submitModal(); await flushPromises();
    expect(document.body.textContent).toContain('JSON 必须为对象');
    expect(document.body.textContent).not.toContain('secret-proposal-token');
    expect(wrapper.emitted('submit')).toBeUndefined();
    wrapper.unmount();
  });
});
