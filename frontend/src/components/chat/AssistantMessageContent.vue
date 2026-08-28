<script setup lang="ts">
import { computed, defineComponent, onErrorCaptured, ref } from 'vue';
import { parseAnswerBlocks, type AnswerBlock } from '@/features/chat/answerBlocks';
import EChartsBlock from './EChartsBlock.vue';
import MermaidBlock from './MermaidBlock.vue';
import SafeMarkdownBlock from './SafeMarkdownBlock.vue';
import SourceFallback from './SourceFallback.vue';

const props = defineProps<{
  content: string;
}>();

const parseResult = computed<{ blocks: AnswerBlock[]; failed: boolean }>(() => {
  try {
    return { blocks: parseAnswerBlocks(props.content), failed: false };
  } catch {
    return { blocks: [], failed: true };
  }
});

const BlockBoundary = defineComponent({
  setup(_, { slots }) {
    const failed = ref(false);
    onErrorCaptured(() => {
      failed.value = true;
      return false;
    });
    return () => (failed.value ? slots.fallback?.() : slots.default?.());
  },
});
</script>

<template>
  <pre v-if="parseResult.failed" class="answer-plain-fallback">{{ props.content }}</pre>
  <template v-else v-for="(block, index) in parseResult.blocks" :key="`${block.type}-${index}`">
    <SafeMarkdownBlock v-if="block.type === 'markdown'" :source="block.source" />
    <BlockBoundary v-else-if="block.type === 'mermaid'">
      <MermaidBlock :source="block.source" />
      <template #fallback>
        <SourceFallback title="Mermaid 图表不可用" :source="block.source" />
      </template>
    </BlockBoundary>
    <BlockBoundary v-else-if="block.type === 'echarts'">
      <EChartsBlock :source="block.source" />
      <template #fallback>
        <SourceFallback title="ECharts 图表不可用" :source="block.source" />
      </template>
    </BlockBoundary>
  </template>
</template>

<style scoped>
.answer-plain-fallback {
  max-width: 100%;
  overflow-x: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
