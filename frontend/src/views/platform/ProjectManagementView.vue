<template>
  <div class="page-grid">
    <a-card class="section-card">
      <div class="toolbar"><div><a-typography-title :level="4" style="margin: 0">单位与项目</a-typography-title><a-typography-text type="secondary">维护当前单位下的项目边界与启用状态。</a-typography-text></div><a-space><a-button :loading="loading" @click="loadProjects">刷新</a-button><a-button type="primary" @click="openCreate">新建项目</a-button></a-space></div>
    </a-card>
    <a-card class="section-card" title="项目列表"><a-alert v-if="errorMessage" type="error" :message="errorMessage" show-icon style="margin-bottom: 16px"/><a-table :columns="columns" :data-source="projects" :loading="loading" row-key="id"><template #bodyCell="{ column, record }"><a-tag v-if="column.key === 'status'" :color="record.status === 'active' ? 'green' : 'default'">{{ record.status === 'active' ? '启用' : '停用' }}</a-tag><a-space v-else-if="column.key === 'action'"><a @click="openEdit(record)">编辑</a><a @click="toggleStatus(record)">{{ record.status === 'active' ? '停用' : '启用' }}</a></a-space></template><template #emptyText><a-empty description="暂无项目" /></template></a-table></a-card>
    <a-modal v-model:open="createOpen" title="新建项目" :confirm-loading="creating" @ok="submitCreate"><a-form layout="vertical"><a-form-item label="项目编码"><a-input v-model:value="form.code" /></a-form-item><a-form-item label="项目名称"><a-input v-model:value="form.name" /></a-form-item></a-form></a-modal>
    <a-modal v-model:open="editOpen" title="编辑项目" :confirm-loading="updating" @ok="submitEdit"><a-form layout="vertical"><a-form-item label="项目名称"><a-input v-model:value="editForm.name" /></a-form-item></a-form></a-modal>
  </div>
</template>
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { ApiError } from '@/api/client';
import { createIdentityProject, listIdentityProjects, setIdentityProjectStatus, updateIdentityProject, type IdentityProject } from '@/api/identity';
const projects = ref<IdentityProject[]>([]); const loading = ref(false); const creating = ref(false); const updating = ref(false); const createOpen = ref(false); const editOpen = ref(false); const errorMessage = ref(''); const form = ref({ code: '', name: '' }); const editForm = ref({ id: '', name: '' }); let controller: AbortController | null = null;
const columns = [{ title: '编码', dataIndex: 'code' }, { title: '项目名称', dataIndex: 'name' }, { title: '状态', key: 'status' }, { title: '操作', key: 'action' }];
async function loadProjects() { controller?.abort(); controller = new AbortController(); loading.value = true; errorMessage.value = ''; try { projects.value = await listIdentityProjects(controller.signal); } catch (error) { if (error instanceof DOMException && error.name === 'AbortError') return; errorMessage.value = error instanceof ApiError ? error.message : '项目加载失败'; } finally { loading.value = false; } }
function openCreate() { form.value = { code: '', name: '' }; createOpen.value = true; }
function openEdit(project: IdentityProject) { editForm.value = { id: project.id, name: project.name }; editOpen.value = true; }
async function submitCreate() { if (!form.value.code.trim() || !form.value.name.trim()) return; creating.value = true; try { await createIdentityProject({ code: form.value.code.trim(), name: form.value.name.trim() }); createOpen.value = false; await loadProjects(); } catch (error) { errorMessage.value = error instanceof ApiError ? error.message : '创建项目失败'; } finally { creating.value = false; } }
async function toggleStatus(project: IdentityProject) { try { await setIdentityProjectStatus(project.id, project.status === 'active' ? 'inactive' : 'active'); await loadProjects(); } catch (error) { errorMessage.value = error instanceof ApiError ? error.message : '更新项目状态失败'; } }
async function submitEdit() { if (!editForm.value.name.trim()) return; updating.value = true; try { await updateIdentityProject(editForm.value.id, { name: editForm.value.name.trim() }); editOpen.value = false; await loadProjects(); } catch (error) { errorMessage.value = error instanceof ApiError ? error.message : '编辑项目失败'; } finally { updating.value = false; } }
onMounted(loadProjects); onBeforeUnmount(() => controller?.abort());
</script>
