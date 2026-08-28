// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => {
  const dispose = vi.fn();
  const resize = vi.fn();
  const setOption = vi.fn();

  return {
    coreImports: 0,
    dispose,
    init: vi.fn(() => ({ dispose, resize, setOption })),
    resize,
    setOption,
    use: vi.fn(),
  };
});

vi.mock('echarts/core', () => {
  mocks.coreImports += 1;
  return { init: mocks.init, use: mocks.use };
});

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

import EChartsBlock from './EChartsBlock.vue';

const validSource = '{"xAxis":{"data":["08:00"]},"series":[{"type":"line","data":[12.3]}]}';
const wrappers: Array<{ unmount: () => void }> = [];

class ResizeObserverMock {
  static instances: ResizeObserverMock[] = [];

  readonly disconnect = vi.fn();
  readonly observe = vi.fn();

  constructor(readonly callback: ResizeObserverCallback) {
    ResizeObserverMock.instances.push(this);
  }

  trigger(): void {
    this.callback([], this as unknown as ResizeObserver);
  }
}

function render(source = validSource) {
  const wrapper = mount(EChartsBlock, { props: { source } });
  wrappers.push(wrapper);
  return wrapper;
}

async function waitForChart(): Promise<void> {
  await vi.waitFor(() => expect(mocks.init).toHaveBeenCalledTimes(1));
  await flushPromises();
}

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  ResizeObserverMock.instances = [];
  mocks.coreImports = 0;
  mocks.dispose.mockReset();
  mocks.init.mockReset();
  mocks.resize.mockReset();
  mocks.setOption.mockReset();
  mocks.use.mockReset();
  mocks.init.mockReturnValue({
    dispose: mocks.dispose,
    resize: mocks.resize,
    setOption: mocks.setOption,
  });
  vi.unstubAllGlobals();
});

describe('EChartsBlock', () => {
  it('sets validated options without merging into an existing chart', async () => {
    vi.stubGlobal('ResizeObserver', ResizeObserverMock);
    const wrapper = render();
    await waitForChart();

    expect(mocks.setOption).toHaveBeenCalledWith({
      xAxis: { data: ['08:00'] },
      series: [{ type: 'line', data: [12.3] }],
    }, { notMerge: true });
    expect(wrapper.attributes('data-state')).toBe('ready');
  });

  it('resizes its chart when the observed host changes size', async () => {
    vi.stubGlobal('ResizeObserver', ResizeObserverMock);
    render();
    await waitForChart();

    ResizeObserverMock.instances[0].trigger();

    expect(mocks.resize).toHaveBeenCalledTimes(1);
  });

  it('disposes the old chart before initializing a changed source', async () => {
    vi.stubGlobal('ResizeObserver', ResizeObserverMock);
    const wrapper = render();
    await waitForChart();

    await wrapper.setProps({ source: '{"series":[{"type":"bar","data":[4]}]}' });
    await vi.waitFor(() => expect(mocks.init).toHaveBeenCalledTimes(2));

    expect(mocks.dispose).toHaveBeenCalledTimes(1);
    expect(mocks.dispose.mock.invocationCallOrder[0])
      .toBeLessThan(mocks.init.mock.invocationCallOrder[1]);
  });

  it('disconnects its observer and disposes its chart on unmount', async () => {
    vi.stubGlobal('ResizeObserver', ResizeObserverMock);
    const wrapper = render();
    await waitForChart();
    const observer = ResizeObserverMock.instances[0];

    wrapper.unmount();

    expect(observer.disconnect).toHaveBeenCalledTimes(1);
    expect(mocks.dispose).toHaveBeenCalledTimes(1);
  });

  it('falls back before loading ECharts when its JSON is invalid', async () => {
    vi.stubGlobal('ResizeObserver', ResizeObserverMock);
    const wrapper = render('{"series":null}');
    await flushPromises();

    expect(wrapper.get('.source-fallback').text()).toContain('ECharts 图表不可用');
    expect(mocks.coreImports).toBe(0);
    expect(mocks.init).not.toHaveBeenCalled();
  });
});
