<script setup lang="ts">
import { EyeOutlined, FileTextOutlined } from '@ant-design/icons-vue';

import type { ArtifactInfo } from '@/api/artifacts';

defineProps<{
  artifacts: ArtifactInfo[];
}>();

const emit = defineEmits<{
  preview: [artifact: ArtifactInfo];
}>();

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}
</script>

<template>
  <div class="assistant-artifact-cards" aria-label="HTML 成果文件">
    <article v-for="artifact in artifacts" :key="artifact.id" class="assistant-artifact-card">
      <span class="artifact-icon" aria-hidden="true"><FileTextOutlined /></span>
      <span class="artifact-details">
        <strong>{{ artifact.filename }}</strong>
        <small>{{ artifact.content_type }} · {{ formatSize(artifact.size_bytes) }}</small>
      </span>
      <button
        type="button"
        class="artifact-preview-button"
        :title="`预览 ${artifact.filename}`"
        :aria-label="`预览 ${artifact.filename}`"
        @click="emit('preview', artifact)"
      >
        <EyeOutlined />
      </button>
    </article>
  </div>
</template>

<style scoped>
.assistant-artifact-cards {
  display: grid;
  gap: 6px;
  margin-top: 10px;
}

.assistant-artifact-card {
  display: grid;
  grid-template-columns: 30px minmax(0, 1fr) 32px;
  gap: 8px;
  min-width: 0;
  padding: 8px;
  align-items: center;
  background: #fff;
  border: 1px solid #d6e3eb;
  border-radius: 5px;
}

.artifact-icon {
  display: grid;
  width: 30px;
  height: 30px;
  place-items: center;
  color: #2563eb;
  background: #edf4ff;
  border-radius: 4px;
}

.artifact-details {
  min-width: 0;
}

.artifact-details strong,
.artifact-details small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.artifact-details strong {
  color: #315064;
  font-size: 13px;
  line-height: 20px;
}

.artifact-details small {
  color: #7d919f;
  font-size: 11px;
  line-height: 18px;
}

.artifact-preview-button {
  display: grid;
  width: 32px;
  height: 32px;
  padding: 0;
  place-items: center;
  color: #315fbb;
  background: #f5f8fc;
  border: 1px solid #d8e3ed;
  border-radius: 4px;
  cursor: pointer;
}

.artifact-preview-button:hover,
.artifact-preview-button:focus-visible {
  color: #fff;
  background: #2563eb;
  border-color: #2563eb;
}
</style>
