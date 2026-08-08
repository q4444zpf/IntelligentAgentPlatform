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
  'a-form-item': { template: '<div><slot /></div>' },
  'a-input': { template: '<input />' },
  'a-input-password': { template: '<input type="password" />' },
  'a-checkbox': { template: '<input type="checkbox" />' },
  'a-segmented': { template: '<div />' },
  'a-button': { template: '<button><slot /></button>' },
  'a-alert': { template: '<div />' },
  'a-modal': { props: ['open'], emits: ['ok', 'cancel'], template: '<div v-if="open"><slot name="title" /><slot /></div>' },
};

beforeEach(() => {
  Object.values(mocks).forEach((mock) => mock.mockReset());
  mocks.localLogin.mockResolvedValue(undefined);
});

describe('LoginView', () => {
  it('uses entered credentials for the primary login even when development identity is configured', async () => {
    const wrapper = mount(LoginView, { global: { stubs } });

    expect(wrapper.text()).toContain('本地账号登录');
    await wrapper.find('form').trigger('submit');
    await flushPromises();

    expect(mocks.localLogin).toHaveBeenCalledWith('admin', '123456');
    expect(mocks.devLogin).not.toHaveBeenCalled();
  });

});
