<template>
  <div class="page-grid">
    <a-card class="section-card">
      <div class="toolbar">
        <div>
          <a-typography-title :level="4" style="margin: 0">角色管理</a-typography-title>
          <a-typography-text type="secondary">查看当前单位可用角色与权限目录。</a-typography-text>
        </div>
        <a-button :loading="loading" @click="loadRoles">刷新</a-button>
      </div>
    </a-card>
    <a-card class="section-card" title="角色列表">
      <a-alert v-if="errorMessage" type="error" show-icon :message="errorMessage" style="margin-bottom: 16px" />
      <a-table :columns="columns" :data-source="roles" :loading="loading" row-key="id" :pagination="{ pageSize: 10 }">
        <template #bodyCell="{ column, record }">
          <a-tag v-if="column.key === 'scope_type'">{{ scopeText(record.scope_type) }}</a-tag>
          <a-tag v-else-if="column.key === 'status'" :color="record.status === 'active' ? 'green' : 'default'">{{ record.status === 'active' ? '启用' : '停用' }}</a-tag>
          <a-tag v-else-if="column.key === 'built_in'" :color="record.built_in ? 'blue' : 'default'">{{ record.built_in ? '内置' : '自定义' }}</a-tag>
          <span v-else-if="column.key === 'action'" class="disabled-action">配置</span>
        </template>
        <template #emptyText><a-empty description="当前单位暂无角色数据" /></template>
      </a-table>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { ApiError } from '@/api/client';
import { listIdentityRoles, type IdentityRole } from '@/api/identity';

const roles = ref<IdentityRole[]>([]);
const loading = ref(false);
const errorMessage = ref('');
let controller: AbortController | null = null;
const columns = [
  { title: '角色名称', dataIndex: 'name' },
  { title: '编码', dataIndex: 'code' },
  { title: '作用域', key: 'scope_type' },
  { title: '类型', key: 'built_in' },
  { title: '状态', key: 'status' },
  { title: '操作', key: 'action' },
];
function scopeText(scope: string): string { return scope === 'platform' ? '平台' : scope === 'project' ? '项目' : '单位'; }
async function loadRoles(): Promise<void> {
  controller?.abort(); controller = new AbortController(); loading.value = true; errorMessage.value = '';
  try { roles.value = await listIdentityRoles(controller.signal); }
  catch (error) { if (error instanceof DOMException && error.name === 'AbortError') return; errorMessage.value = error instanceof ApiError ? error.message : '角色数据加载失败'; }
  finally { loading.value = false; }
}
onMounted(loadRoles); onBeforeUnmount(() => controller?.abort());
</script>

<style scoped>
.disabled-action { color: rgba(0, 0, 0, 0.25); cursor: not-allowed; pointer-events: none; }
</style>
