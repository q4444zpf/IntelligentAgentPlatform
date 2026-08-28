<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { EChartsValidationError, parseEChartsOptions } from '@/features/chat/echartsOptions';
import SourceFallback from './SourceFallback.vue';

const { source } = defineProps<{
  source: string;
}>();

type RenderState = 'loading' | 'ready' | 'error';
type ChartInstance = {
  dispose: () => void;
  resize: () => void;
  setOption: (option: Record<string, unknown>, settings: { notMerge: boolean }) => void;
};

const state = ref<RenderState>('loading');
const error = ref('');
const host = ref<HTMLElement>();
let chart: ChartInstance | undefined;
let observer: ResizeObserver | undefined;
let renderVersion = 0;

function disposeChart(): void {
  observer?.disconnect();
  observer = undefined;
  chart?.dispose();
  chart = undefined;
}

async function renderChart(sourceSnapshot: string): Promise<void> {
  const version = ++renderVersion;
  disposeChart();

  let option: Record<string, unknown>;
  try {
    option = parseEChartsOptions(sourceSnapshot);
  } catch (value) {
    error.value = value instanceof EChartsValidationError ? value.message : 'ECharts 图表配置无效';
    state.value = 'error';
    return;
  }

  state.value = 'loading';
  error.value = '';

  try {
    const [core, charts, components, renderers] = await Promise.all([
      import('echarts/core'),
      import('echarts/charts'),
      import('echarts/components'),
      import('echarts/renderers'),
    ]);
    if (version !== renderVersion || !host.value) return;

    core.use([
      charts.LineChart,
      charts.BarChart,
      charts.ScatterChart,
      components.GridComponent,
      components.TooltipComponent,
      components.LegendComponent,
      components.TitleComponent,
      components.DatasetComponent,
      renderers.CanvasRenderer,
    ]);

    const instance = core.init(host.value) as ChartInstance;
    if (version !== renderVersion) {
      instance.dispose();
      return;
    }

    chart = instance;
    chart.setOption(option, { notMerge: true });
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => chart?.resize());
      observer.observe(host.value);
    }
    state.value = 'ready';
  } catch {
    if (version === renderVersion) {
      error.value = 'ECharts 图表渲染失败';
      state.value = 'error';
    }
  }
}

onMounted(() => {
  watch(() => source, (nextSource) => {
    void renderChart(nextSource);
  }, { immediate: true });
});

onBeforeUnmount(() => {
  renderVersion += 1;
  disposeChart();
});
</script>

<template>
  <section class="echarts-block" :data-state="state">
    <div ref="host" class="echarts-host" />
    <div v-if="state === 'loading'" role="status">图表加载中</div>
    <div v-else-if="state === 'error'" class="echarts-error">
      <p>{{ error }}</p>
      <SourceFallback title="ECharts 图表不可用" :source="source" />
    </div>
  </section>
</template>

<style scoped>
.echarts-block {
  min-width: 0;
}

.echarts-host {
  min-height: 320px;
  height: clamp(320px, 42vh, 480px);
  width: 100%;
}
</style>
