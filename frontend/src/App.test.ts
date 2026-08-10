// @vitest-environment happy-dom
import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  refreshSession: vi.fn().mockResolvedValue({}),
  replace: vi.fn(),
}));
const wrappers: Array<{ unmount: () => void }> = [];

vi.mock('@/stores/permission', () => ({
  usePermissionStore: () => ({
    isAuthenticated: true,
    refreshSession: mocks.refreshSession,
  }),
}));

vi.mock('vue-router', () => ({
  RouterView: { template: '<div />' },
  useRoute: () => ({ path: '/mcp', fullPath: '/mcp?tab=tools' }),
  useRouter: () => ({ replace: mocks.replace }),
}));

import App from './App.vue';

function render() {
  const wrapper = mount(App, { global: { stubs: { RouterView: true } } });
  wrappers.push(wrapper);
  return wrapper;
}

describe('App session lifecycle', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-08-10T00:00:00Z'));
    mocks.refreshSession.mockClear();
    mocks.replace.mockClear();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  });

  afterEach(() => {
    wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
    vi.useRealTimers();
  });

  it('refreshes a visible authenticated session after recent activity', async () => {
    render();
    window.dispatchEvent(new Event('pointerdown'));

    await vi.advanceTimersByTimeAsync(60_000);

    expect(mocks.refreshSession).toHaveBeenCalledTimes(1);
  });

  it('does not refresh while the page is hidden', async () => {
    render();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });

    await vi.advanceTimersByTimeAsync(60_000);

    expect(mocks.refreshSession).not.toHaveBeenCalled();
  });

  it('does not refresh after the activity threshold expires', async () => {
    render();
    await vi.advanceTimersByTimeAsync(300_000);
    mocks.refreshSession.mockClear();
    await vi.advanceTimersByTimeAsync(60_000);

    expect(mocks.refreshSession).not.toHaveBeenCalled();
  });

  it('redirects only once when multiple session invalid events arrive', () => {
    render();
    window.dispatchEvent(new CustomEvent('iap:session-invalid'));
    window.dispatchEvent(new CustomEvent('iap:session-invalid'));

    expect(mocks.replace).toHaveBeenCalledTimes(1);
    expect(mocks.replace).toHaveBeenCalledWith({ path: '/login', query: { redirect: '/mcp?tab=tools' } });
  });

  it('removes listeners and timer when unmounted', async () => {
    const wrapper = render();
    mocks.replace.mockClear();
    wrapper.unmount();
    window.dispatchEvent(new Event('pointerdown'));
    await vi.advanceTimersByTimeAsync(60_000);
    window.dispatchEvent(new CustomEvent('iap:session-invalid'));

    expect(mocks.refreshSession).not.toHaveBeenCalled();
    expect(mocks.replace).not.toHaveBeenCalled();
  });
});
