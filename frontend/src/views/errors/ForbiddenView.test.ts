// @vitest-environment happy-dom

import { flushPromises, mount } from '@vue/test-utils';
import { defineComponent } from 'vue';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  logout: vi.fn(),
  replace: vi.fn(),
}));

vi.mock('@/stores/permission', () => ({
  usePermissionStore: () => ({ logout: mocks.logout }),
}));

vi.mock('vue-router', () => ({
  useRouter: () => ({ replace: mocks.replace }),
}));

import ForbiddenView from './ForbiddenView.vue';

const wrappers: Array<{ unmount: () => void }> = [];
const ButtonStub = defineComponent({
  emits: ['click'],
  template: '<button @click="$emit(\'click\')"><slot /></button>',
});

function render() {
  const wrapper = mount(ForbiddenView, {
    global: {
      stubs: {
        'a-result': { template: '<section><slot name="extra" /></section>' },
        'a-button': ButtonStub,
      },
    },
  });
  wrappers.push(wrapper);
  return wrapper;
}

describe('ForbiddenView', () => {
  beforeEach(() => {
    mocks.logout.mockReset().mockResolvedValue(undefined);
    mocks.replace.mockReset().mockResolvedValue(undefined);
  });

  afterEach(() => {
    wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  });

  it('logs out and returns to login so another account can sign in', async () => {
    const wrapper = render();

    await wrapper.get('button').trigger('click');
    await flushPromises();

    expect(mocks.logout).toHaveBeenCalledOnce();
    expect(mocks.replace).toHaveBeenCalledWith('/login');
  });

  it('still returns to login when the server logout request fails', async () => {
    mocks.logout.mockRejectedValueOnce(new Error('network unavailable'));
    const wrapper = render();

    await wrapper.get('button').trigger('click');
    await flushPromises();

    expect(mocks.replace).toHaveBeenCalledWith('/login');
  });
});
