<script setup lang="ts">
import DOMPurify from 'dompurify';
import { onBeforeUnmount, ref, watch } from 'vue';
import { loadMermaid } from '@/features/chat/mermaidLoader';
import { MAX_MERMAID_EDGES, MermaidPolicyError, validateMermaidSource } from '@/features/chat/mermaidPolicy';
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
  const sanitized = DOMPurify.sanitize(value, {
    FORBID_TAGS: ['a', 'animate', 'animateMotion', 'animateTransform', 'foreignObject', 'image', 'script', 'set'],
    USE_PROFILES: { html: false, mathMl: false, svg: true, svgFilters: true },
  });
  const template = document.createElement('template');
  template.innerHTML = sanitized;
  const localReference = /^#[A-Za-z_][\w:.-]*$/;
  const scrubCss = (css: string) => css
    .replace(/@import[^;]*(?:;|$)/gi, '')
    .replace(/url\(\s*(['"]?)([^'")]+)\1\s*\)/gi, (match, _quote: string, target: string) => (
      localReference.test(target.trim()) ? match : 'none'
    ));

  template.content.querySelectorAll('style').forEach((style) => {
    style.textContent = scrubCss(style.textContent ?? '');
  });
  const removableElements = new Set(['animate', 'animatemotion', 'animatetransform', 'foreignobject', 'image', 'script', 'set']);
  [...template.content.querySelectorAll('*')].forEach((element) => {
    const tagName = element.tagName.toLowerCase();
    if (tagName === 'a') {
      element.replaceWith(...element.childNodes);
      return;
    }
    if (removableElements.has(tagName)) {
      element.remove();
      return;
    }
    for (const attribute of [...element.attributes]) {
      const name = attribute.name.toLowerCase();
      if (name === 'src' || name === 'data' || name === 'poster') {
        element.removeAttribute(attribute.name);
      } else if ((name === 'href' || name === 'xlink:href') && !localReference.test(attribute.value.trim())) {
        element.removeAttribute(attribute.name);
      } else if (/url\s*\(/i.test(attribute.value)) {
        element.setAttribute(attribute.name, scrubCss(attribute.value));
      }
    }
  });
  return template.innerHTML;
}

async function renderDiagram(sourceSnapshot: string): Promise<void> {
  const version = ++renderVersion;
  clearActiveTimeout();
  state.value = 'loading';
  error.value = '';
  svg.value = '';

  try {
    validateMermaidSource(sourceSnapshot);
  } catch (value) {
    error.value = value instanceof MermaidPolicyError ? value.message : '图表源码不符合安全策略';
    state.value = 'error';
    return;
  }

  let timeoutId: number | undefined;
  try {
    const { default: mermaid } = await loadMermaid();
    if (version !== renderVersion) return;

    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      htmlLabels: false,
      flowchart: { htmlLabels: false },
      class: { htmlLabels: false },
      maxEdges: MAX_MERMAID_EDGES,
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
