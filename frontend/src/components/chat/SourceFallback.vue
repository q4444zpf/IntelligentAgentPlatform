<script setup lang="ts">
import { ref } from 'vue';
import { copyText } from '@/utils/clipboard';

const { source } = defineProps<{
  title: string;
  source: string;
}>();

const sourceVisible = ref(false);

function toggleSource(): void {
  sourceVisible.value = !sourceVisible.value;
}

function copySource(): void {
  void copyText(source);
}
</script>

<template>
  <section class="source-fallback">
    <p class="source-fallback-title">{{ title }}</p>
    <div class="source-fallback-actions">
      <button
        type="button"
        :aria-label="sourceVisible ? '隐藏源码' : '查看源码'"
        :aria-expanded="sourceVisible"
        @click="toggleSource"
      >
        {{ sourceVisible ? '隐藏源码' : '查看源码' }}
      </button>
      <button type="button" aria-label="复制源码" @click="copySource">复制源码</button>
    </div>
    <pre v-if="sourceVisible">{{ source }}</pre>
  </section>
</template>

<style scoped>
.source-fallback {
  min-width: 0;
}

.source-fallback-actions {
  display: flex;
  gap: 0.5rem;
}

.source-fallback pre {
  max-width: 100%;
  overflow-x: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
