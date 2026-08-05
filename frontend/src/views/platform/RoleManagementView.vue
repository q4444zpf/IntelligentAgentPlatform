<template>
  <div class="page-grid">
    <a-card class="section-card">
      <div class="toolbar">
        <div>
          <a-typography-title :level="4" style="margin: 0">角色管理</a-typography-title>
          <a-typography-text type="secondary">查看当前单位可用角色与权限目录。</a-typography-text>
        </div>
        <a-space><a-button :loading="loading" @click="loadRoles">刷新</a-button><a-button type="primary" @click="openCreate">新建角色</a-button></a-space>
      </div>
    </a-card>
    <a-card class="section-card" title="角色列表">
      <a-alert v-if="errorMessage" type="error" show-icon :message="errorMessage" style="margin-bottom: 16px" />
      <a-table :columns="columns" :data-source="roles" :loading="loading" row-key="id" :pagination="{ pageSize: 10 }">
        <template #bodyCell="{ column, record }">
          <a-tag v-if="column.key === 'scope_type'">{{ scopeText(record.scope_type) }}</a-tag>
          <a-tag v-else-if="column.key === 'status'" :color="record.status === 'active' ? 'green' : 'default'">{{ record.status === 'active' ? '启用' : '停用' }}</a-tag>
          <a-tag v-else-if="column.key === 'built_in'" :color="record.built_in ? 'blue' : 'default'">{{ record.built_in ? '内置' : '自定义' }}</a-tag>
          <a v-else-if="column.key === 'action'" :class="{ 'disabled-action': record.built_in }" @click="toggleStatus(record)">{{ record.built_in ? '内置' : (record.status === 'active' ? '停用' : '启用') }}</a>
        </template>
        <template #emptyText><a-empty description="当前单位暂无角色数据" /></template>
      </a-table>
    </a-card>
  </div>
  <a-modal v-model:open="createOpen" title="新建角色" :confirm-loading="creating" @ok="submitCreate"><a-form layout="vertical"><a-form-item label="角色编码"><a-input v-model:value="form.code" /></a-form-item><a-form-item label="角色名称"><a-input v-model:value="form.name" /></a-form-item><a-form-item label="作用域"><a-select v-model:value="form.scope_type" :options="[{ label: '单位', value: 'unit' }, { label: '项目', value: 'project' }]" /></a-form-item></a-form></a-modal>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { ApiError } from '@/api/client';
import { createIdentityRole, listIdentityRoles, setIdentityRoleStatus, type IdentityRole } from '@/api/identity';

const roles = ref<IdentityRole[]>([]);
const loading = ref(false);
const errorMessage = ref('');
let controller: AbortController | null = null;
const createOpen = ref(false); const creating = ref(false); const form = ref<{ code: string; name: string; scope_type: 'unit' | 'project' }>({ code: '', name: '', scope_type: 'unit' });
const columns = [
  { title: '角色名称', dataIndex: 'name' },
  { title: '编码', dataIndex: 'code' },
  { title: '作用域', key: 'scope_type' },
  { title: '类型', key: 'built_in' },
  { title: '状态', key: 'status' },
  { title: '操作', key: 'action' },
];
function scopeText(scope: string): string { return scope === 'platform' ? '平台' : scope === 'project' ? '项目' : '单位'; }
function openCreate(): void { form.value = { code: '', name: '', scope_type: 'unit' }; createOpen.value = true; }
async function submitCreate(): Promise<void> { if (!form.value.code.trim() || !form.value.name.trim()) return; creating.value = true; try { await createIdentityRole(form.value); createOpen.value = false; await loadRoles(); } catch (error) { errorMessage.value = error instanceof ApiError ? error.message : '创建角色失败'; } finally { creating.value = false; } }
async function toggleStatus(role: IdentityRole): Promise<void> { if (role.built_in) return; try { await setIdentityRoleStatus(role.id, role.status === 'active' ? 'inactive' : 'active'); await loadRoles(); } catch (error) { errorMessage.value = error instanceof ApiError ? error.message : '更新角色状态失败'; } }
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
