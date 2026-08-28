<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, onUpdated, useTemplateRef } from 'vue';
import { copyText } from '@/utils/clipboard';
import { renderSafeMarkdown } from '@/features/chat/safeMarkdown';

const { source } = defineProps<{
  source: string;
}>();

const content = useTemplateRef<HTMLElement>('content');
const renderedHtml = computed(() => renderSafeMarkdown(source));
const removeListeners: Array<() => void> = [];

function clearDecorations(): void {
  removeListeners.splice(0).forEach((removeListener) => removeListener());
}

function decorateCodeBlock(pre: HTMLPreElement): void {
  const code = pre.querySelector('code')?.textContent ?? pre.textContent ?? '';
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'code-copy-button';
  button.setAttribute('aria-label', '复制代码');
  button.textContent = '复制';
  const handleCopy = () => {
    void copyText(code);
  };

  button.addEventListener('click', handleCopy);
  pre.prepend(button);
  removeListeners.push(() => button.removeEventListener('click', handleCopy));
}

function decorateImage(image: HTMLImageElement): void {
  const handleError = () => {
    const fallback = document.createElement('div');
    fallback.className = 'image-fallback';
    fallback.textContent = '图片加载失败';
    image.replaceWith(fallback);
  };

  image.addEventListener('error', handleError, { once: true });
  removeListeners.push(() => image.removeEventListener('error', handleError));
}

function decorateContent(): void {
  clearDecorations();
  const element = content.value;
  if (!element) return;

  element.querySelectorAll<HTMLPreElement>('pre').forEach(decorateCodeBlock);
  element.querySelectorAll<HTMLImageElement>('img').forEach(decorateImage);
}

onMounted(decorateContent);
onUpdated(decorateContent);
onBeforeUnmount(clearDecorations);
</script>

<template>
  <div ref="content" class="safe-markdown" v-html="renderedHtml" />
</template>

<style scoped>
.safe-markdown {
  min-width: 0;
  max-width: 100%;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.safe-markdown :deep(img) {
  display: block;
  max-width: 100%;
  height: auto;
}

.safe-markdown :deep(pre),
.safe-markdown :deep(table) {
  max-width: 100%;
  overflow-x: auto;
}

.safe-markdown :deep(pre) {
  position: relative;
  padding-top: 2.5rem;
}

.safe-markdown :deep(table) {
  display: block;
}

.safe-markdown :deep(.code-copy-button) {
  position: absolute;
  top: 0.5rem;
  right: 0.5rem;
}

.safe-markdown :deep(.image-fallback) {
  display: flex;
  height: 10rem;
  max-width: 100%;
  align-items: center;
  justify-content: center;
}
</style>
