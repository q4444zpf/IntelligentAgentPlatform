<script setup lang="ts">
import {
  CloseOutlined,
  DownloadOutlined,
  ExportOutlined,
  FullscreenExitOutlined,
  FullscreenOutlined,
  LoadingOutlined,
  ReloadOutlined,
} from '@ant-design/icons-vue';
import { onBeforeUnmount, ref, watch } from 'vue';

import { artifactsApi, type ArtifactInfo } from '@/api/artifacts';

const props = defineProps<{
  artifact: ArtifactInfo;
}>();

const emit = defineEmits<{
  close: [];
}>();

const previewUrl = ref('');
const loading = ref(false);
const error = ref('');
const commandError = ref('');
const isFullscreen = ref(false);
let previewController: AbortController | undefined;
let downloadController: AbortController | undefined;
let requestVersion = 0;

watch(() => props.artifact.id, loadPreview, { immediate: true });

function isAbortError(value: unknown): boolean {
  return value instanceof Error && value.name === 'AbortError';
}

async function loadPreview() {
  const version = ++requestVersion;
  previewController?.abort();
  const controller = new AbortController();
  previewController = controller;
  loading.value = true;
  error.value = '';
  commandError.value = '';
  previewUrl.value = '';

  try {
    const result = await artifactsApi.download(props.artifact.id, controller.signal);
    if (version === requestVersion && !controller.signal.aborted) previewUrl.value = result.url;
  } catch (value) {
    if (version === requestVersion && !isAbortError(value)) error.value = 'HTML 预览加载失败';
  } finally {
    if (version === requestVersion) loading.value = false;
  }
}

async function downloadArtifact() {
  downloadController?.abort();
  const controller = new AbortController();
  downloadController = controller;
  commandError.value = '';
  try {
    const result = await artifactsApi.download(props.artifact.id, controller.signal);
    if (controller.signal.aborted) return;
    const link = document.createElement('a');
    link.href = result.url;
    link.download = props.artifact.filename;
    link.style.display = 'none';
    document.body.appendChild(link);
    try {
      link.click();
    } finally {
      link.remove();
    }
  } catch (value) {
    if (!isAbortError(value)) commandError.value = 'HTML 文件下载失败';
  }
}

function openNewWindow() {
  if (previewUrl.value) window.open(previewUrl.value, '_blank', 'noopener,noreferrer');
}

onBeforeUnmount(() => {
  requestVersion += 1;
  previewController?.abort();
  downloadController?.abort();
});
</script>

<template>
  <aside class="html-artifact-preview" :class="{ 'is-fullscreen': isFullscreen }">
    <header class="preview-toolbar">
      <div class="preview-heading">
        <strong>HTML 预览</strong>
        <span :title="props.artifact.filename">{{ props.artifact.filename }}</span>
      </div>
      <div class="preview-actions">
        <button type="button" title="刷新 HTML 预览" aria-label="刷新 HTML 预览" :disabled="loading" @click="loadPreview">
          <ReloadOutlined />
        </button>
        <button
          type="button"
          :title="isFullscreen ? '退出全屏 HTML 预览' : '全屏 HTML 预览'"
          :aria-label="isFullscreen ? '退出全屏 HTML 预览' : '全屏 HTML 预览'"
          @click="isFullscreen = !isFullscreen"
        >
          <FullscreenExitOutlined v-if="isFullscreen" />
          <FullscreenOutlined v-else />
        </button>
        <button type="button" title="在新窗口打开 HTML 预览" aria-label="在新窗口打开 HTML 预览" :disabled="!previewUrl" @click="openNewWindow">
          <ExportOutlined />
        </button>
        <button type="button" :title="`下载 ${props.artifact.filename}`" :aria-label="`下载 ${props.artifact.filename}`" @click="downloadArtifact">
          <DownloadOutlined />
        </button>
        <button type="button" title="关闭 HTML 预览" aria-label="关闭 HTML 预览" @click="emit('close')">
          <CloseOutlined />
        </button>
      </div>
    </header>

    <div class="preview-body">
      <div v-if="loading" class="preview-state" role="status">
        <LoadingOutlined spin />
        <span>HTML 预览加载中</span>
      </div>
      <div v-else-if="error" class="preview-state preview-error" role="alert">
        <strong>{{ error }}</strong>
        <div>
          <button type="button" aria-label="重试 HTML 预览" @click="loadPreview"><ReloadOutlined /> 重试</button>
          <button type="button" aria-label="关闭 HTML 预览" @click="emit('close')"><CloseOutlined /> 关闭</button>
        </div>
      </div>
      <iframe
        v-else-if="previewUrl"
        :key="previewUrl"
        :src="previewUrl"
        sandbox=""
        referrerpolicy="no-referrer"
        :title="`HTML 预览：${props.artifact.filename}`"
      />
    </div>
    <p v-if="commandError" class="preview-command-error" role="alert">{{ commandError }}</p>
  </aside>
</template>

<style scoped>
.html-artifact-preview {
  position: relative;
  display: grid;
  min-width: 0;
  min-height: 0;
  grid-template-rows: auto minmax(0, 1fr);
  overflow: hidden;
  background: #fff;
  border-left: 1px solid #dce7ef;
}

.preview-toolbar {
  display: flex;
  min-height: 54px;
  gap: 10px;
  padding: 8px 10px 8px 14px;
  align-items: center;
  justify-content: space-between;
  background: #f8fbfd;
  border-bottom: 1px solid #dce7ef;
}

.preview-heading {
  min-width: 0;
}

.preview-heading strong,
.preview-heading span {
  display: block;
}

.preview-heading strong {
  color: #27475e;
  font-size: 14px;
  line-height: 20px;
}

.preview-heading span {
  overflow: hidden;
  color: #738896;
  font-size: 11px;
  line-height: 16px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.preview-actions {
  display: flex;
  flex: none;
  gap: 3px;
}

.preview-actions button {
  display: grid;
  width: 32px;
  height: 32px;
  padding: 0;
  place-items: center;
  color: #526f82;
  background: #fff;
  border: 1px solid #d7e2ea;
  border-radius: 4px;
  cursor: pointer;
}

.preview-actions button:hover:not(:disabled),
.preview-actions button:focus-visible {
  color: #245fc3;
  border-color: #8fb1e8;
}

.preview-actions button:disabled {
  opacity: .45;
  cursor: not-allowed;
}

.preview-body {
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: #eef3f6;
}

.preview-body iframe {
  display: block;
  width: 100%;
  height: 100%;
  background: #fff;
  border: 0;
}

.preview-state {
  display: grid;
  height: 100%;
  padding: 24px;
  place-content: center;
  justify-items: center;
  gap: 10px;
  box-sizing: border-box;
  color: #617a8a;
}

.preview-state > :deep(svg) {
  width: 20px;
  height: 20px;
}

.preview-error strong {
  color: #9e3434;
}

.preview-error > div {
  display: flex;
  gap: 8px;
}

.preview-error button {
  display: inline-flex;
  min-height: 32px;
  padding: 0 10px;
  align-items: center;
  gap: 5px;
  color: #345c77;
  background: #fff;
  border: 1px solid #cfdce5;
  border-radius: 4px;
  cursor: pointer;
}

.preview-command-error {
  position: absolute;
  right: 12px;
  bottom: 12px;
  max-width: calc(100% - 24px);
  margin: 0;
  padding: 7px 10px;
  color: #8e3030;
  background: #fff2f0;
  border: 1px solid #ffccc7;
  border-radius: 4px;
  font-size: 12px;
}

.html-artifact-preview.is-fullscreen {
  position: fixed;
  z-index: 1200;
  inset: 0;
  width: 100vw;
  height: 100vh;
  height: 100dvh;
  border: 0;
  border-radius: 0;
}

@media (max-width: 900px) {
  .html-artifact-preview {
    position: fixed;
    z-index: 1200;
    display: block;
    inset: 0;
    width: 100vw;
    height: 100vh;
    height: 100dvh;
    border: 0;
    border-radius: 0;
  }

  .preview-toolbar {
    position: fixed;
    z-index: 1;
    top: 0;
    right: 0;
    left: 0;
    height: 56px;
    box-sizing: border-box;
  }

  .preview-body {
    height: 100%;
    padding-top: 56px;
    box-sizing: border-box;
  }
}
</style>
