// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  copyText: vi.fn<(text: string) => Promise<void>>().mockResolvedValue(undefined),
  initialize: vi.fn(),
  loadMermaid: vi.fn(),
  render: vi.fn(),
}));
const wrappers: Array<{ unmount: () => void }> = [];

vi.mock('@/utils/clipboard', () => ({
  copyText: mocks.copyText,
}));

vi.mock('mermaid', () => {
  return {
    default: {
      initialize: mocks.initialize,
      render: mocks.render,
    },
  };
});

vi.mock('@/features/chat/mermaidLoader', () => ({
  loadMermaid: mocks.loadMermaid,
}));

async function render(source: string) {
  const { default: MermaidBlock } = await import('./MermaidBlock.vue');
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

async function expectFallbackWithSource(wrapper: Awaited<ReturnType<typeof render>>, source: string) {
  expect(wrapper.get('[data-state="error"]').text()).toContain('Mermaid 图表不可用');
  expect(wrapper.find('pre').exists()).toBe(false);

  await wrapper.get('button[aria-label="查看源码"]').trigger('click');
  expect(wrapper.get('pre').text()).toBe(source);

  await wrapper.get('button[aria-label="复制源码"]').trigger('click');
  expect(mocks.copyText).toHaveBeenLastCalledWith(source);
}

beforeEach(() => {
  vi.resetModules();
  mocks.loadMermaid.mockReset();
  mocks.loadMermaid.mockResolvedValue({
    default: {
      initialize: mocks.initialize,
      render: mocks.render,
    },
  });
});

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

    const first = await render('graph LR\nA-->B');
    await vi.waitFor(() => expect(mocks.render).toHaveBeenCalledTimes(1));
    const second = await render('graph LR\nB-->C');
    await vi.waitFor(() => expect(mocks.render).toHaveBeenCalledTimes(2));
    await flushPromises();

    expect(mocks.initialize).toHaveBeenCalledWith(expect.objectContaining({
      securityLevel: 'strict',
      htmlLabels: false,
      flowchart: { htmlLabels: false },
      class: { htmlLabels: false },
      maxEdges: 120,
      startOnLoad: false,
    }));
    expect(mocks.loadMermaid).toHaveBeenCalledTimes(2);
    expect(first.get('[data-state="ready"]').html()).toContain('<svg');
    expect(second.get('[data-state="ready"]').html()).toContain('<svg');
    expect(mocks.render.mock.calls[0][0]).not.toBe(mocks.render.mock.calls[1][0]);
  });

  it('re-sanitizes Mermaid SVG before putting it in the owned container', async () => {
    mocks.render.mockResolvedValue({
      svg: '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><defs><filter id="safe-filter"></filter></defs><rect fill="url(https://attacker.example/fill.svg)" filter="url(#safe-filter)"></rect><image href="https://attacker.example/pixel.png"></image><a href="https://attacker.example/"><text>linked</text></a><animate attributeName="x"></animate><foreignObject><div>bad</div></foreignObject><style>@import url(https://attacker.example/a.css); .safe{filter:url(#safe-filter)}</style><text>ok</text></svg>',
    });

    const wrapper = await render('graph LR\nA-->B');
    await flushPromises();

    const output = wrapper.get('[data-state="ready"]').html();
    expect(output).toContain('<svg');
    expect(output).toContain('filter="url(#safe-filter)"');
    expect(output).not.toMatch(/<(?:script|image|foreignObject|a|animate)\b/i);
    expect(output).not.toContain('onload=');
    expect(output).not.toContain('attacker.example');
  });

  it.each([
    ['configuration directive', '%%{init: {"flowchart":{"htmlLabels":true}}}%%\nflowchart LR\nA-->B'],
    ['click navigation', 'flowchart LR\nA-->B\nclick A href "https://attacker.example"'],
    ['image node', 'flowchart LR\nA@{ img: "https://attacker.example/pixel.png", label: "A" }'],
    ['unsupported syntax', 'sequenceDiagram\nAlice->>Bob: hello'],
  ])('rejects %s before importing Mermaid', async (_, source) => {
    const wrapper = await render(source);
    await flushPromises();

    expect(wrapper.attributes('data-state')).toBe('error');
    expect(mocks.loadMermaid).not.toHaveBeenCalled();
    expect(mocks.initialize).not.toHaveBeenCalled();
    expect(mocks.render).not.toHaveBeenCalled();
    await expectFallbackWithSource(wrapper, source);
  });

  it('rejects excessive graph complexity before importing Mermaid', async () => {
    const source = ['flowchart LR', ...Array(121).fill('A-->B')].join('\n');
    const wrapper = await render(source);
    await flushPromises();

    expect(wrapper.attributes('data-state')).toBe('error');
    expect(mocks.loadMermaid).not.toHaveBeenCalled();
    expect(mocks.render).not.toHaveBeenCalled();
  });

  it('falls back for a UTF-8 source over the Mermaid byte limit', async () => {
    const source = '水'.repeat(34_134);
    const wrapper = await render(source);
    await flushPromises();

    expect(mocks.render).not.toHaveBeenCalled();
    await expectFallbackWithSource(wrapper, source);
  });

  it('falls back when Mermaid rejects the source', async () => {
    mocks.render.mockRejectedValue(new Error('parse failed'));
    const source = 'graph LR\nA[无法渲染]-->B';
    const wrapper = await render(source);
    await flushPromises();

    await expectFallbackWithSource(wrapper, source);
    expect(wrapper.get('pre').text()).toBe(source);
  });

  it('times out after five seconds, clears its timer, and keeps source controls usable', async () => {
    vi.useFakeTimers();
    const clearTimeout = vi.spyOn(window, 'clearTimeout');
    mocks.render.mockReturnValue(new Promise(() => undefined));
    const source = 'graph LR\nA-->B';
    const wrapper = await render(source);
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
    const wrapper = await render('graph LR\nA-->B');
    await flushPromises();

    wrapper.unmount();
    pending.resolve({ svg: '<svg><text>late</text></svg>' });
    await flushPromises();

    expect(clearTimeout).toHaveBeenCalled();
    expect(wrapper.html()).not.toContain('late');
  });

  it('renders only the replacement source when the initial render completes late', async () => {
    const initial = deferred<{ svg: string }>();
    const replacement = deferred<{ svg: string }>();
    mocks.render
      .mockReturnValueOnce(initial.promise)
      .mockReturnValueOnce(replacement.promise);
    const wrapper = await render('graph LR\nA-->B');
    await vi.waitFor(() => expect(mocks.render).toHaveBeenCalledTimes(1));

    await wrapper.setProps({ source: 'graph LR\nB-->C' });
    await vi.waitFor(() => expect(mocks.render).toHaveBeenCalledTimes(2));
    initial.resolve({ svg: '<svg><text>initial</text></svg>' });
    replacement.resolve({ svg: '<svg><text>replacement</text></svg>' });
    await flushPromises();

    expect(wrapper.get('[data-state="ready"]').html()).toContain('replacement');
    expect(wrapper.html()).not.toContain('initial');
  });

  it('ignores a stale rejection after the replacement diagram is ready', async () => {
    const initial = deferred<{ svg: string }>();
    mocks.render
      .mockReturnValueOnce(initial.promise)
      .mockResolvedValueOnce({ svg: '<svg><text>replacement</text></svg>' });
    const wrapper = await render('graph LR\nA-->B');
    await vi.waitFor(() => expect(mocks.render).toHaveBeenCalledTimes(1));

    await wrapper.setProps({ source: 'graph LR\nB-->C' });
    await vi.waitFor(() => expect(wrapper.attributes('data-state')).toBe('ready'));
    initial.reject(new Error('late failure'));
    await flushPromises();

    expect(wrapper.get('[data-state="ready"]').html()).toContain('replacement');
  });

  it('rejects an oversized replacement source without rendering it', async () => {
    const initial = deferred<{ svg: string }>();
    mocks.render.mockReturnValue(initial.promise);
    const wrapper = await render('graph LR\nA-->B');
    await vi.waitFor(() => expect(mocks.render).toHaveBeenCalledTimes(1));
    const oversized = '水'.repeat(34_134);

    await wrapper.setProps({ source: oversized });
    await flushPromises();
    initial.resolve({ svg: '<svg><text>initial</text></svg>' });
    await flushPromises();

    expect(mocks.render).toHaveBeenCalledTimes(1);
    expect(wrapper.get('[data-state="error"]').text()).toContain('图表源码超过 100 KB 限制');
    expect(wrapper.html()).not.toContain('initial');
  });
});
