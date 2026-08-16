<template>
  <div class="artifact-page">
    <a-alert
      v-if="error"
      type="error"
      message="成果文件加载失败"
      :description="error"
      show-icon
    >
      <template #action>
        <a-button aria-label="重试成果文件" @click="loadArtifacts">重试</a-button>
      </template>
    </a-alert>

    <header class="artifact-heading">
      <div>
        <div class="heading-label">ARTIFACTS</div>
        <h2>成果文件</h2>
        <p>查看当前项目可访问的运行成果，并从 MinIO 下载文件。</p>
      </div>
      <a-button aria-label="刷新成果文件" :loading="loading" @click="loadArtifacts">
        <template #icon><ReloadOutlined /></template>
      </a-button>
    </header>

    <a-spin :spinning="loading">
      <a-table
        v-if="artifacts.length"
        :columns="columns"
        :data-source="artifacts"
        row-key="id"
        :pagination="{ pageSize: 20 }"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'filename'">
            <strong>{{ record.filename }}</strong>
            <small>{{ record.content_type }}</small>
          </template>
          <template v-else-if="column.key === 'run_id'">
            <code>{{ record.run_id || '-' }}</code>
          </template>
          <template v-else-if="column.key === 'size'">
            {{ formatSize(record.size_bytes) }}
          </template>
          <template v-else-if="column.key === 'created_at'">
            {{ formatTime(record.created_at) }}
          </template>
          <template v-else-if="column.key === 'action'">
            <a-button
              type="link"
              :loading="downloadingId === record.id"
              :aria-label="`下载 ${record.filename}`"
              @click="download(record)"
            >下载</a-button>
          </template>
        </template>
      </a-table>
      <a-empty v-else-if="!loading && !error" description="暂无可下载的成果文件" />
    </a-spin>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { ReloadOutlined } from '@ant-design/icons-vue';

import { artifactsApi, type ArtifactInfo } from '@/api/artifacts';
import { ApiError } from '@/api/client';

const artifacts = ref<ArtifactInfo[]>([]);
const loading = ref(false);
const error = ref('');
const downloadingId = ref('');
let controller: AbortController | undefined;

const columns = [
  { title: '文件名', key: 'filename' },
  { title: 'Run ID', key: 'run_id', width: 280 },
  { title: '大小', key: 'size', width: 110 },
  { title: '创建时间', key: 'created_at', width: 180 },
  { title: '操作', key: 'action', width: 100 },
];

function errorText(value: unknown) {
  return value instanceof ApiError ? value.message : value instanceof Error ? value.message : '加载失败';
}

async function loadArtifacts() {
  controller?.abort();
  controller = new AbortController();
  loading.value = true;
  error.value = '';
  try {
    artifacts.value = await artifactsApi.list(controller.signal);
  } catch (value) {
    if (!(value instanceof DOMException && value.name === 'AbortError')) error.value = errorText(value);
  } finally {
    loading.value = false;
  }
}

async function download(artifact: ArtifactInfo) {
  downloadingId.value = artifact.id;
  try {
    const result = await artifactsApi.download(artifact.id);
    const link = document.createElement('a');
    link.href = result.url;
    link.download = artifact.filename;
    link.style.display = 'none';
    document.body.appendChild(link);
    link.click();
    link.remove();
  } catch (value) {
    error.value = errorText(value);
  } finally {
    downloadingId.value = '';
  }
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleString('zh-CN');
}

onMounted(loadArtifacts);
onBeforeUnmount(() => controller?.abort());
</script>

<style scoped>
.artifact-page { display: grid; gap: 16px; min-width: 0; }
.artifact-heading { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 16px 20px; background: #fff; border: 1px solid #e1e9f2; border-left: 4px solid #17856b; border-radius: 8px; }
.heading-label { color: #17856b; font: 700 10px Consolas, monospace; letter-spacing: 0; }
.artifact-heading h2 { margin: 2px 0 0; font-size: 20px; }
.artifact-heading p { margin: 5px 0 0; color: #667085; }
:deep(.ant-table-cell strong), :deep(.ant-table-cell small) { display: block; }
:deep(.ant-table-cell small) { color: #98a2b3; font-size: 11px; }
:deep(code) { color: #667085; font-size: 11px; }
</style>
