<script setup lang="ts">
import { LoadingOutlined, ReloadOutlined } from '@ant-design/icons-vue';
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useRoute } from 'vue-router';

import { artifactsApi } from '@/api/artifacts';
import SandboxedArtifactFrame from '@/components/chat/SandboxedArtifactFrame.vue';

const route = useRoute();
const artifactId = computed(() => (
  typeof route.params.artifactId === 'string' && route.params.artifactId.trim()
    ? route.params.artifactId
    : ''
));
const previewUrl = ref('');
const loading = ref(false);
const error = ref('');
let controller: AbortController | undefined;
let requestVersion = 0;

function isAbortError(value: unknown): boolean {
  return value instanceof Error && value.name === 'AbortError';
}

async function loadPreview() {
  const version = ++requestVersion;
  controller?.abort();
  previewUrl.value = '';
  error.value = '';
  if (!artifactId.value) {
    loading.value = false;
    error.value = 'HTML 预览地址无效';
    return;
  }

  const nextController = new AbortController();
  controller = nextController;
  loading.value = true;
  try {
    const result = await artifactsApi.preview(artifactId.value, nextController.signal);
    if (version === requestVersion && !nextController.signal.aborted) previewUrl.value = result.url;
  } catch (value) {
    if (version === requestVersion && !isAbortError(value)) error.value = 'HTML 预览加载失败';
  } finally {
    if (version === requestVersion) loading.value = false;
  }
}

watch(artifactId, () => void loadPreview(), { immediate: true });

onBeforeUnmount(() => {
  requestVersion += 1;
  controller?.abort();
});
</script>

<template>
  <main class="artifact-preview-view">
    <header class="artifact-preview-toolbar">
      <div>
        <strong>HTML 成果预览</strong>
        <span>{{ artifactId }}</span>
      </div>
      <button type="button" title="刷新 HTML 预览" aria-label="刷新 HTML 预览" :disabled="loading || !artifactId" @click="loadPreview">
        <ReloadOutlined />
      </button>
    </header>
    <section class="artifact-preview-body">
      <div v-if="loading" class="artifact-preview-state" role="status">
        <LoadingOutlined spin />
        <span>HTML 预览加载中</span>
      </div>
      <div v-else-if="error" class="artifact-preview-state is-error" role="alert">
        <strong>{{ error }}</strong>
        <button v-if="artifactId" type="button" aria-label="重试 HTML 预览" @click="loadPreview">
          <ReloadOutlined /> 重试
        </button>
      </div>
      <SandboxedArtifactFrame
        v-else-if="previewUrl"
        :src="previewUrl"
        :title="`HTML 预览：${artifactId}`"
      />
    </section>
  </main>
</template>

<style scoped>
.artifact-preview-view {
  display: grid;
  min-width: 0;
  height: 100vh;
  height: 100dvh;
  grid-template-rows: 56px minmax(0, 1fr);
  overflow: hidden;
  color: #243b4a;
  background: #eef3f6;
}

.artifact-preview-toolbar {
  display: flex;
  min-width: 0;
  padding: 8px 14px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  box-sizing: border-box;
  background: #fff;
  border-bottom: 1px solid #dce7ef;
}

.artifact-preview-toolbar > div {
  min-width: 0;
}

.artifact-preview-toolbar strong,
.artifact-preview-toolbar span {
  display: block;
}

.artifact-preview-toolbar strong {
  font-size: 14px;
  line-height: 20px;
}

.artifact-preview-toolbar span {
  overflow: hidden;
  color: #738896;
  font: 11px/16px Consolas, monospace;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.artifact-preview-toolbar button,
.artifact-preview-state button {
  display: inline-flex;
  min-width: 32px;
  min-height: 32px;
  padding: 0 9px;
  align-items: center;
  justify-content: center;
  gap: 5px;
  color: #526f82;
  background: #fff;
  border: 1px solid #d7e2ea;
  border-radius: 4px;
  cursor: pointer;
}

.artifact-preview-toolbar button:disabled {
  opacity: .45;
  cursor: not-allowed;
}

.artifact-preview-body {
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

.artifact-preview-state {
  display: grid;
  height: 100%;
  padding: 24px;
  place-content: center;
  justify-items: center;
  gap: 10px;
  box-sizing: border-box;
  color: #617a8a;
}

.artifact-preview-state.is-error strong {
  color: #9e3434;
}
</style>
