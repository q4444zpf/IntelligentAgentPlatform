// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  copyText: vi.fn<(text: string) => Promise<void>>().mockResolvedValue(undefined),
  initialize: vi.fn(),
  render: vi.fn(),
}));
const wrappers: Array<{ unmount: () => void }> = [];

vi.mock('@/utils/clipboard', () => ({
  copyText: mocks.copyText,
}));

vi.mock('mermaid', () => ({
  default: {
    initialize: mocks.initialize,
    render: mocks.render,
  },
}));

import MermaidBlock from './MermaidBlock.vue';

function render(source: string) {
  const wrapper = mount(MermaidBlock, { props: { source } });
  wrappers.push(wrapper);
  return wrapper;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

async function expectFallbackWithSource(wrapper: ReturnType<typeof render>, source: string) {
  expect(wrapper.get('[data-state="error"]').text()).toContain('Mermaid 图表不可用');
  expect(wrapper.find('pre').exists()).toBe(false);

  await wrapper.get('button[aria-label="查看源码"]').trigger('click');
  expect(wrapper.get('pre').text()).toBe(source);

  await wrapper.get('button[aria-label="复制源码"]').trigger('click');
  expect(mocks.copyText).toHaveBeenLastCalledWith(source);
}

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  mocks.copyText.mockClear();
  mocks.initialize.mockClear();
  mocks.render.mockReset();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('MermaidBlock', () => {
  it('uses strict Mermaid configuration and a unique render id', async () => {
    mocks.render.mockResolvedValue({ svg: '<svg><text>ok</text></svg>' });

    const first = render('graph LR\nA-->B');
    await vi.waitFor(() => expect(mocks.render).toHaveBeenCalledTimes(1));
    const second = render('graph LR\nB-->C');
    await vi.waitFor(() => expect(mocks.render).toHaveBeenCalledTimes(2));
    await flushPromises();

    expect(mocks.initialize).toHaveBeenCalledWith(expect.objectContaining({
      securityLevel: 'strict',
      htmlLabels: false,
      startOnLoad: false,
    }));
    expect(first.get('[data-state="ready"]').html()).toContain('<svg');
    expect(second.get('[data-state="ready"]').html()).toContain('<svg');
    expect(mocks.render.mock.calls[0][0]).not.toBe(mocks.render.mock.calls[1][0]);
  });

  it('re-sanitizes Mermaid SVG before putting it in the owned container', async () => {
    mocks.render.mockResolvedValue({
      svg: '<svg onload="alert(1)"><script>alert(1)</script><text>ok</text></svg>',
    });

    const wrapper = render('graph LR\nA-->B');
    await flushPromises();

    expect(wrapper.get('[data-state="ready"]').html()).toContain('<svg');
    expect(wrapper.html()).not.toContain('<script');
    expect(wrapper.html()).not.toContain('onload=');
  });

  it('falls back for a UTF-8 source over the Mermaid byte limit', async () => {
    const source = '水'.repeat(34_134);
    const wrapper = render(source);
    await flushPromises();

    expect(mocks.render).not.toHaveBeenCalled();
    await expectFallbackWithSource(wrapper, source);
  });

  it('falls back when Mermaid rejects the source', async () => {
    mocks.render.mockRejectedValue(new Error('parse failed'));
    const source = 'graph LR\nA--invalid-->B\n<img src=x onerror=alert(1)>';
    const wrapper = render(source);
    await flushPromises();

    await expectFallbackWithSource(wrapper, source);
    expect(wrapper.get('pre').html()).not.toContain('<img');
  });

  it('times out after five seconds, clears its timer, and keeps source controls usable', async () => {
    vi.useFakeTimers();
    const clearTimeout = vi.spyOn(window, 'clearTimeout');
    mocks.render.mockReturnValue(new Promise(() => undefined));
    const source = 'graph LR\nA-->B';
    const wrapper = render(source);
    await flushPromises();

    await vi.advanceTimersByTimeAsync(5_000);
    await flushPromises();

    expect(wrapper.get('[data-state="error"]').text()).toContain('图表渲染超时');
    expect(clearTimeout).toHaveBeenCalled();
    await expectFallbackWithSource(wrapper, source);
  });

  it('ignores a late render result after unmount and clears its outstanding timer', async () => {
    const pending = deferred<{ svg: string }>();
    const clearTimeout = vi.spyOn(window, 'clearTimeout');
    mocks.render.mockReturnValue(pending.promise);
    const wrapper = render('graph LR\nA-->B');
    await flushPromises();

    wrapper.unmount();
    pending.resolve({ svg: '<svg><text>late</text></svg>' });
    await flushPromises();

    expect(clearTimeout).toHaveBeenCalled();
    expect(wrapper.html()).not.toContain('late');
  });
});
