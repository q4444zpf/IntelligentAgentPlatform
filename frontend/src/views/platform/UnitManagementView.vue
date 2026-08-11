<template>
  <div class="page-grid">
    <a-card class="section-card">
      <div class="toolbar"><div><a-typography-title :level="4" style="margin: 0">单位管理</a-typography-title><a-typography-text type="secondary">维护当前身份边界的单位信息。</a-typography-text></div><a-button :loading="loading" @click="loadUnits">刷新</a-button></div>
    </a-card>
    <a-card class="section-card" title="单位列表">
      <a-alert v-if="errorMessage" type="error" :message="errorMessage" show-icon style="margin-bottom: 16px" />
      <a-table :columns="columns" :data-source="units" :loading="loading" row-key="id">
        <template #bodyCell="{ column, record }"><a-tag v-if="column.key === 'status'" :color="record.status === 'active' ? 'green' : 'default'">{{ record.status === 'active' ? '启用' : '停用' }}</a-tag><a v-else-if="column.key === 'action'" @click="openEdit(record)">编辑</a></template>
      </a-table>
    </a-card>
    <a-modal v-model:open="editOpen" title="编辑单位" :confirm-loading="updating" @ok="submitEdit"><a-form layout="vertical"><a-form-item label="单位编码"><a-input :value="editForm.code" disabled /></a-form-item><a-form-item label="单位名称"><a-input v-model:value="editForm.name" /></a-form-item></a-form></a-modal>
  </div>
</template>
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { ApiError } from '@/api/client';
import { listIdentityUnits, updateIdentityUnit, type IdentityUnit } from '@/api/identity';
const units = ref<IdentityUnit[]>([]); const loading = ref(false); const updating = ref(false); const editOpen = ref(false); const errorMessage = ref(''); const editForm = ref({ id: '', code: '', name: '' }); let controller: AbortController | null = null;
const columns = [{ title: '编码', dataIndex: 'code' }, { title: '单位名称', dataIndex: 'name' }, { title: '状态', key: 'status' }, { title: '操作', key: 'action' }];
async function loadUnits() { controller?.abort(); controller = new AbortController(); loading.value = true; errorMessage.value = ''; try { units.value = await listIdentityUnits(controller.signal); } catch (error) { if (error instanceof DOMException && error.name === 'AbortError') return; errorMessage.value = error instanceof ApiError ? error.message : '单位加载失败'; } finally { loading.value = false; } }
function openEdit(unit: IdentityUnit) { editForm.value = { id: unit.id, code: unit.code, name: unit.name }; editOpen.value = true; }
async function submitEdit() { if (!editForm.value.name.trim() || updating.value) return; updating.value = true; try { await updateIdentityUnit(editForm.value.id, { name: editForm.value.name.trim() }); editOpen.value = false; await loadUnits(); } catch (error) { errorMessage.value = error instanceof ApiError ? error.message : '编辑单位失败'; } finally { updating.value = false; } }
onMounted(loadUnits); onBeforeUnmount(() => controller?.abort());
</script>
