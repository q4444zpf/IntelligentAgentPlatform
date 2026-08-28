<script setup lang="ts">
import DOMPurify from 'dompurify';
import { onBeforeUnmount, ref, watch } from 'vue';
import { MAX_MERMAID_BYTES } from '@/features/chat/answerBlocks';
import SourceFallback from './SourceFallback.vue';

const MERMAID_TIMEOUT_MS = 5_000;

const { source } = defineProps<{
  source: string;
}>();

type RenderState = 'loading' | 'ready' | 'error';

const state = ref<RenderState>('loading');
const error = ref('');
const svg = ref('');
let renderVersion = 0;
let activeTimeoutId: number | undefined;

function clearActiveTimeout(): void {
  if (activeTimeoutId === undefined) return;
  window.clearTimeout(activeTimeoutId);
  activeTimeoutId = undefined;
}

function sanitizeSvg(value: string): string {
  return DOMPurify.sanitize(value, {
    USE_PROFILES: { html: false, mathMl: false, svg: true, svgFilters: true },
  });
}

async function renderDiagram(sourceSnapshot: string): Promise<void> {
  const version = ++renderVersion;
  clearActiveTimeout();
  state.value = 'loading';
  error.value = '';
  svg.value = '';

  if (new TextEncoder().encode(sourceSnapshot).byteLength > MAX_MERMAID_BYTES) {
    error.value = '图表源码超过 100 KB 限制';
    state.value = 'error';
    return;
  }

  let timeoutId: number | undefined;
  try {
    const { default: mermaid } = await import('mermaid');
    if (version !== renderVersion) return;

    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      htmlLabels: false,
    });
    const id = `assistant-mermaid-${crypto.randomUUID()}`;
    const timeout = new Promise<never>((_, reject) => {
      timeoutId = window.setTimeout(() => reject(new Error('timeout')), MERMAID_TIMEOUT_MS);
      activeTimeoutId = timeoutId;
    });
    const result = await Promise.race([mermaid.render(id, sourceSnapshot), timeout]);

    if (version === renderVersion) {
      svg.value = sanitizeSvg(result.svg);
      state.value = 'ready';
    }
  } catch (value) {
    if (version === renderVersion) {
      error.value = value instanceof Error && value.message === 'timeout'
        ? '图表渲染超时'
        : '图表语法无效';
      state.value = 'error';
    }
  } finally {
    if (timeoutId !== undefined) window.clearTimeout(timeoutId);
    if (activeTimeoutId === timeoutId) activeTimeoutId = undefined;
  }
}

watch(() => source, (nextSource) => {
  void renderDiagram(nextSource);
}, { immediate: true });

onBeforeUnmount(() => {
  renderVersion += 1;
  clearActiveTimeout();
});
</script>

<template>
  <section class="mermaid-block" :data-state="state">
    <div v-if="state === 'loading'" role="status">图表加载中</div>
    <div v-else-if="state === 'ready'" class="mermaid-output" v-html="svg" />
    <div v-else class="mermaid-error">
      <p>{{ error }}</p>
      <SourceFallback title="Mermaid 图表不可用" :source="source" />
    </div>
  </section>
</template>

<style scoped>
.mermaid-block {
  min-width: 0;
}

.mermaid-output {
  max-width: 100%;
  overflow-x: auto;
}

.mermaid-output :deep(svg) {
  display: block;
  max-width: 100%;
  height: auto;
}
</style>
