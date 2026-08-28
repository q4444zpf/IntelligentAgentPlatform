// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => {
  const dispose = vi.fn();
  const resize = vi.fn();
  const setOption = vi.fn();

  return {
    coreImports: 0,
    dispose,
    init: vi.fn(() => ({ dispose, resize, setOption })),
    loadECharts: vi.fn(),
    resize,
    setOption,
    use: vi.fn(),
  };
});

vi.mock('echarts/core', () => {
  mocks.coreImports += 1;
  return { init: mocks.init, use: mocks.use };
});

vi.mock('@/features/chat/echartsLoader', () => ({
  loadECharts: mocks.loadECharts,
}));

vi.mock('echarts/charts', () => ({
  BarChart: {},
  LineChart: {},
  ScatterChart: {},
}));

vi.mock('echarts/components', () => ({
  DatasetComponent: {},
  GridComponent: {},
  LegendComponent: {},
  TitleComponent: {},
  TooltipComponent: {},
}));

vi.mock('echarts/renderers', () => ({ CanvasRenderer: {} }));

const validSource = '{"xAxis":{"data":["08:00"]},"series":[{"type":"line","data":[12.3]}]}';
const wrappers: Array<{ unmount: () => void }> = [];

function echartsModules() {
  return {
    charts: { BarChart: {}, LineChart: {}, ScatterChart: {} },
    components: {
      DatasetComponent: {},
      GridComponent: {},
      LegendComponent: {},
      TitleComponent: {},
      TooltipComponent: {},
    },
    core: { init: mocks.init, use: mocks.use },
    renderers: { CanvasRenderer: {} },
  };
}

function deferredEChartsModules() {
  let resolvePromise!: (value: ReturnType<typeof echartsModules>) => void;
  const promise = new Promise<ReturnType<typeof echartsModules>>((resolve) => {
    resolvePromise = resolve;
  });
  return { promise, resolve: () => resolvePromise(echartsModules()) };
}

class ResizeObserverMock {
  static instances: ResizeObserverMock[] = [];
  static observeError: Error | undefined;

  readonly disconnect = vi.fn();
  readonly observe = vi.fn(() => {
    if (ResizeObserverMock.observeError) throw ResizeObserverMock.observeError;
  });

  constructor(readonly callback: ResizeObserverCallback) {
    ResizeObserverMock.instances.push(this);
  }

  trigger(): void {
    this.callback([], this as unknown as ResizeObserver);
  }
}

async function render(source = validSource) {
  const { default: EChartsBlock } = await import('./EChartsBlock.vue');
  const wrapper = mount(EChartsBlock, { props: { source } });
  wrappers.push(wrapper);
  return wrapper;
}

async function waitForChart(): Promise<void> {
  await vi.waitFor(() => expect(mocks.init).toHaveBeenCalledTimes(1));
  await flushPromises();
}

beforeEach(() => {
  vi.resetModules();
  mocks.coreImports = 0;
  mocks.dispose.mockReset();
  mocks.init.mockReset();
  mocks.loadECharts.mockReset();
  mocks.resize.mockReset();
  mocks.setOption.mockReset();
  mocks.use.mockReset();
  mocks.init.mockReturnValue({
    dispose: mocks.dispose,
    resize: mocks.resize,
    setOption: mocks.setOption,
  });
  mocks.loadECharts.mockResolvedValue(echartsModules());
});

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  ResizeObserverMock.instances = [];
  ResizeObserverMock.observeError = undefined;
  vi.unstubAllGlobals();
});

describe('EChartsBlock', () => {
  it('sets validated options without merging into an existing chart', async () => {
    vi.stubGlobal('ResizeObserver', ResizeObserverMock);
    const wrapper = await render();
    await waitForChart();

    expect(mocks.setOption).toHaveBeenCalledWith({
      animation: false,
      xAxis: { data: ['08:00'] },
      series: [{ type: 'line', data: [12.3] }],
    }, { notMerge: true });
    expect(wrapper.attributes('data-state')).toBe('ready');
    expect(mocks.loadECharts).toHaveBeenCalledTimes(1);
  });

  it('resizes its chart when the observed host changes size', async () => {
    vi.stubGlobal('ResizeObserver', ResizeObserverMock);
    await render();
    await waitForChart();

    ResizeObserverMock.instances[0].trigger();

    expect(mocks.resize).toHaveBeenCalledTimes(1);
  });

  it('disposes the old chart before initializing a changed source', async () => {
    vi.stubGlobal('ResizeObserver', ResizeObserverMock);
    const wrapper = await render();
    await waitForChart();

    await wrapper.setProps({ source: '{"series":[{"type":"bar","data":[4]}]}' });
    await vi.waitFor(() => expect(mocks.init).toHaveBeenCalledTimes(2));

    expect(mocks.dispose).toHaveBeenCalledTimes(1);
    expect(mocks.dispose.mock.invocationCallOrder[0])
      .toBeLessThan(mocks.init.mock.invocationCallOrder[1]);
  });

  it('disconnects its observer and disposes its chart on unmount', async () => {
    vi.stubGlobal('ResizeObserver', ResizeObserverMock);
    const wrapper = await render();
    await waitForChart();
    const observer = ResizeObserverMock.instances[0];

    wrapper.unmount();

    expect(observer.disconnect).toHaveBeenCalledTimes(1);
    expect(mocks.dispose).toHaveBeenCalledTimes(1);
  });

  it('falls back before loading ECharts when its JSON is invalid', async () => {
    vi.stubGlobal('ResizeObserver', ResizeObserverMock);
    const wrapper = await render('{"series":null}');
    await flushPromises();

    expect(wrapper.get('.source-fallback').text()).toContain('ECharts 图表不可用');
    expect(mocks.loadECharts).not.toHaveBeenCalled();
    expect(mocks.init).not.toHaveBeenCalled();
  });

  it('falls back before loading ECharts for an image-backed option', async () => {
    vi.stubGlobal('ResizeObserver', ResizeObserverMock);
    const wrapper = await render('{"backgroundColor":{"image":"https://attacker.example/pixel.png"}}');
    await flushPromises();

    expect(wrapper.get('.source-fallback').text()).toContain('ECharts 图表不可用');
    expect(mocks.loadECharts).not.toHaveBeenCalled();
    expect(mocks.init).not.toHaveBeenCalled();
    expect(mocks.setOption).not.toHaveBeenCalled();
  });

  it('does not initialize after an import completes following unmount', async () => {
    const pending = deferredEChartsModules();
    mocks.loadECharts.mockReturnValue(pending.promise);
    const wrapper = await render();
    await vi.waitFor(() => expect(mocks.loadECharts).toHaveBeenCalledTimes(1));

    wrapper.unmount();
    pending.resolve();
    await flushPromises();

    expect(mocks.init).not.toHaveBeenCalled();
    expect(mocks.setOption).not.toHaveBeenCalled();
  });

  it('initializes only the replacement source when a shared import completes late', async () => {
    vi.stubGlobal('ResizeObserver', ResizeObserverMock);
    const pending = deferredEChartsModules();
    mocks.loadECharts.mockReturnValue(pending.promise);
    const wrapper = await render();
    await vi.waitFor(() => expect(mocks.loadECharts).toHaveBeenCalledTimes(1));

    await wrapper.setProps({ source: '{"series":[{"type":"bar","data":[4]}]}' });
    pending.resolve();
    await vi.waitFor(() => expect(mocks.init).toHaveBeenCalledTimes(1));

    expect(mocks.setOption).toHaveBeenCalledWith({
      animation: false,
      series: [{ type: 'bar', data: [4] }],
    }, { notMerge: true });
  });

  it('disposes its chart when setOption throws after initialization', async () => {
    vi.stubGlobal('ResizeObserver', ResizeObserverMock);
    mocks.setOption.mockImplementationOnce(() => {
      throw new Error('set option failed');
    });
    const wrapper = await render();

    await vi.waitFor(() => expect(wrapper.attributes('data-state')).toBe('error'));

    expect(mocks.dispose).toHaveBeenCalledTimes(1);
    expect(ResizeObserverMock.instances).toHaveLength(0);
  });

  it('disposes its chart when constructing ResizeObserver throws', async () => {
    class ThrowingResizeObserver {
      constructor() {
        throw new Error('observer construction failed');
      }
    }

    vi.stubGlobal('ResizeObserver', ThrowingResizeObserver);
    const wrapper = await render();

    await vi.waitFor(() => expect(wrapper.attributes('data-state')).toBe('error'));

    expect(mocks.dispose).toHaveBeenCalledTimes(1);
  });

  it('disconnects its observer and disposes its chart when observe throws', async () => {
    vi.stubGlobal('ResizeObserver', ResizeObserverMock);
    const wrapper = await render();
    await vi.waitFor(() => expect(ResizeObserverMock.instances).toHaveLength(1));
    const initialObserver = ResizeObserverMock.instances[0];
    ResizeObserverMock.observeError = new Error('observe failed');

    await wrapper.setProps({ source: '{"series":[{"type":"bar","data":[4]}]}' });
    await vi.waitFor(() => expect(wrapper.attributes('data-state')).toBe('error'));
    const failingObserver = ResizeObserverMock.instances[1];

    expect(initialObserver.disconnect).toHaveBeenCalledTimes(1);
    expect(failingObserver.disconnect).toHaveBeenCalledTimes(1);
    expect(mocks.dispose).toHaveBeenCalledTimes(2);
  });
});
