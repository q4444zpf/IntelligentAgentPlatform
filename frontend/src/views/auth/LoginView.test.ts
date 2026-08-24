// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import LoginView from './LoginView.vue';

const mocks = vi.hoisted(() => ({
  localLogin: vi.fn(),
  devLogin: vi.fn(),
  oidcLogin: vi.fn(),
  replace: vi.fn(),
  changePassword: vi.fn(),
}));

vi.mock('@/api/client', () => ({
  identityHeaders: {
    'X-Unit-ID': 'test-unit',
    'X-User-ID': 'test-user',
    'X-Project-ID': 'test-project',
  },
}));
vi.mock('@/api/auth', () => ({
  authApi: { changePassword: mocks.changePassword },
}));
vi.mock('@/stores/permission', () => ({
  usePermissionStore: () => ({
    loginWithLocalCredentials: mocks.localLogin,
    loginWithDevelopmentIdentity: mocks.devLogin,
    startOidcLogin: mocks.oidcLogin,
  }),
}));
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ replace: mocks.replace }),
}));
vi.mock('ant-design-vue', () => ({ message: { success: vi.fn(), error: vi.fn() } }));

const stubs = {
  'a-tag': { template: '<span><slot /></span>' },
  'a-form': { emits: ['finish'], template: '<form @submit.prevent="$emit(\'finish\')"><slot /></form>' },
  'a-form-item': { props: ['label'], template: '<label>{{ label }}<slot /></label>' },
  'a-input': { props: ['value'], emits: ['update:value'], template: '<input :value="value" @input="$emit(\'update:value\', $event.target.value)" />' },
  'a-input-password': { props: ['value'], emits: ['update:value'], template: '<input type="password" :value="value" @input="$emit(\'update:value\', $event.target.value)" />' },
  'a-checkbox': { template: '<input type="checkbox" />' },
  'a-segmented': { template: '<div />' },
  'a-button': { template: '<button><slot /></button>' },
  'a-alert': { template: '<div />' },
  'a-modal': { props: ['open'], emits: ['ok', 'cancel'], template: '<div v-if="open"><slot name="title" /><slot /><button class="modal-ok" @click="$emit(\'ok\')">确认修改</button></div>' },
};

beforeEach(() => {
  Object.values(mocks).forEach((mock) => mock.mockReset());
  mocks.localLogin.mockResolvedValue(undefined);
});

describe('LoginView', () => {
  it('uses entered credentials for the primary login even when development identity is configured', async () => {
    const wrapper = mount(LoginView, { global: { stubs } });

    expect(wrapper.text()).toContain('本地账号登录');
    await wrapper.find('input:not([type="password"])').setValue('alice@example.test');
    await wrapper.find('input[type="password"]').setValue('Password123!');
    await wrapper.find('form').trigger('submit');
    await flushPromises();

    expect(mocks.localLogin).toHaveBeenCalledWith('alice@example.test', 'Password123!');
    expect(mocks.devLogin).not.toHaveBeenCalled();
  });

  it('labels the local login identifier as email and does not prefill credentials', () => {
    const wrapper = mount(LoginView, { global: { stubs } });

    expect(wrapper.text()).toContain('邮箱');
    expect((wrapper.find('input:not([type="password"])').element as HTMLInputElement).value).toBe('');
    expect((wrapper.find('input[type="password"]').element as HTMLInputElement).value).toBe('');
  });

  it('rejects a first-login password shorter than the backend minimum', async () => {
    mocks.localLogin.mockResolvedValue({ must_change_password: true });
    const wrapper = mount(LoginView, { global: { stubs } });

    await wrapper.find('input:not([type="password"])').setValue('alice@example.test');
    await wrapper.find('input[type="password"]').setValue('InitialPassword123!');
    await wrapper.find('form').trigger('submit');
    await flushPromises();
    const passwordInputs = wrapper.findAll('input[type="password"]');
    await passwordInputs[2].setValue('short-pass1');
    await passwordInputs[3].setValue('short-pass1');
    await wrapper.find('button.modal-ok').trigger('click');
    await flushPromises();

    expect(mocks.changePassword).not.toHaveBeenCalled();
  });

  it('logs in again with the email and new password after the required change', async () => {
    mocks.localLogin
      .mockResolvedValueOnce({ must_change_password: true })
      .mockResolvedValueOnce({ must_change_password: false });
    mocks.changePassword.mockResolvedValue({ status: 'ok' });
    const wrapper = mount(LoginView, { global: { stubs } });

    await wrapper.find('input:not([type="password"])').setValue('alice@example.test');
    await wrapper.find('input[type="password"]').setValue('InitialPassword123!');
    await wrapper.find('form').trigger('submit');
    await flushPromises();
    const passwordInputs = wrapper.findAll('input[type="password"]');
    await passwordInputs[2].setValue('ChangedPassword123!');
    await passwordInputs[3].setValue('ChangedPassword123!');
    await wrapper.find('button.modal-ok').trigger('click');
    await flushPromises();

    expect(mocks.changePassword).toHaveBeenCalledWith({
      current_password: 'InitialPassword123!',
      new_password: 'ChangedPassword123!',
    });
    expect(mocks.localLogin).toHaveBeenNthCalledWith(2, 'alice@example.test', 'ChangedPassword123!');
    expect(mocks.replace).toHaveBeenCalledWith('/dashboard');
  });

});
