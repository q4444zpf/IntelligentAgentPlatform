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
          <template v-else-if="column.key === 'action'">
            <a-typography-text v-if="record.built_in" type="secondary">内置角色不可删除</a-typography-text>
            <a-space v-else>
              <a :class="{ 'disabled-action': isRoleOperating(record.id) }" @click="openPermissions(record)">权限管理</a>
              <a :class="{ 'disabled-action': isRoleOperating(record.id) }" @click="toggleStatus(record)">{{ record.status === 'active' ? '停用' : '启用' }}</a>
              <a-popconfirm title="删除角色会同时移除该角色的用户绑定和权限授予，是否继续？" @confirm="deleteRole(record)">
                <a :class="{ 'disabled-action': isRoleOperating(record.id) }">{{ removingRoleId === record.id ? '删除中' : '删除' }}</a>
              </a-popconfirm>
            </a-space>
          </template>
        </template>
        <template #emptyText><a-empty description="当前单位暂无角色数据" /></template>
      </a-table>
    </a-card>
  </div>
  <a-modal v-model:open="createOpen" title="新建角色" :confirm-loading="creating" @ok="submitCreate"><a-form layout="vertical"><a-form-item label="角色编码"><a-input v-model:value="form.code" /></a-form-item><a-form-item label="角色名称"><a-input v-model:value="form.name" /></a-form-item><a-form-item label="作用域"><a-select v-model:value="form.scope_type" :options="[{ label: '单位', value: 'unit' }, { label: '项目', value: 'project' }]" /></a-form-item></a-form></a-modal>
  <a-modal v-model:open="permissionOpen" title="角色权限" :footer="null" @cancel="closePermissions">
    <a-alert type="info" show-icon message="当前后端仅提供权限目录查询和授权接口；尚未提供单角色已授权权限查询及撤销接口。" style="margin-bottom: 16px" />
    <a-alert v-if="permissionError" type="error" show-icon :message="permissionError" style="margin-bottom: 16px" />
    <a-form layout="vertical">
      <a-form-item label="权限目录"><a-select v-model:value="permissionCode" :loading="permissionLoading" :options="permissionOptions()" placeholder="请选择要授予的权限" /></a-form-item>
      <a-form-item label="数据范围"><a-select v-model:value="permissionScope" :options="permissionScopeOptions()" /></a-form-item>
    </a-form>
    <a-space><a-button @click="closePermissions">取消</a-button><a-button type="primary" :loading="granting" :disabled="!permissionCode" @click="submitPermissionGrant">授权</a-button></a-space>
  </a-modal>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { ApiError } from '@/api/client';
import {
  createIdentityRole,
  deleteIdentityRole,
  grantIdentityRolePermission,
  listIdentityPermissions,
  listIdentityRoles,
  setIdentityRoleStatus,
  type IdentityPermission,
  type IdentityRole,
} from '@/api/identity';

const roles = ref<IdentityRole[]>([]);
const loading = ref(false);
const errorMessage = ref('');
let controller: AbortController | null = null;
let permissionController: AbortController | null = null;
const createOpen = ref(false); const creating = ref(false); const form = ref<{ code: string; name: string; scope_type: 'unit' | 'project' }>({ code: '', name: '', scope_type: 'unit' });
const removingRoleId = ref<string | null>(null);
const updatingRoleId = ref<string | null>(null);
const permissionOpen = ref(false);
const permissionLoading = ref(false);
const permissionError = ref('');
const granting = ref(false);
const permissionRole = ref<IdentityRole | null>(null);
const permissions = ref<IdentityPermission[]>([]);
const permissionCode = ref('');
const permissionScope = ref<'unit' | 'assigned_projects' | 'project' | 'own'>('unit');
const columns = [
  { title: '角色名称', dataIndex: 'name' },
  { title: '编码', dataIndex: 'code' },
  { title: '作用域', key: 'scope_type' },
  { title: '类型', key: 'built_in' },
  { title: '状态', key: 'status' },
  { title: '操作', key: 'action' },
];
function scopeText(scope: string): string { return scope === 'platform' ? '平台' : scope === 'project' ? '项目' : '单位'; }
const permissionOptions = (): Array<{ label: string; value: string }> => permissions.value.filter((permission) => permission.status === 'active').map((permission) => ({ label: `${permission.code}（${permission.resource}.${permission.action}）`, value: permission.code }));
const permissionScopeOptions = (): Array<{ label: string; value: 'unit' | 'assigned_projects' | 'project' | 'own' }> => {
  const options: Array<{ label: string; value: 'unit' | 'assigned_projects' | 'project' | 'own' }> = [
    { label: '单位范围', value: 'unit' },
    { label: '已分配项目', value: 'assigned_projects' },
    { label: '本人数据', value: 'own' },
  ];
  if (permissionRole.value?.scope_type === 'project') options.splice(1, 0, { label: '当前项目', value: 'project' });
  return options;
};
function isRoleOperating(roleId: string): boolean { return removingRoleId.value === roleId || updatingRoleId.value === roleId; }
function openCreate(): void { form.value = { code: '', name: '', scope_type: 'unit' }; createOpen.value = true; }
async function submitCreate(): Promise<void> { if (!form.value.code.trim() || !form.value.name.trim()) return; creating.value = true; try { await createIdentityRole(form.value); createOpen.value = false; await loadRoles(); } catch (error) { errorMessage.value = error instanceof ApiError ? error.message : '创建角色失败'; } finally { creating.value = false; } }
async function toggleStatus(role: IdentityRole): Promise<void> {
  if (role.built_in || isRoleOperating(role.id)) return;
  updatingRoleId.value = role.id;
  try { await setIdentityRoleStatus(role.id, role.status === 'active' ? 'inactive' : 'active'); await loadRoles(); }
  catch (error) { errorMessage.value = error instanceof ApiError ? error.message : '更新角色状态失败'; }
  finally { updatingRoleId.value = null; }
}
async function deleteRole(role: IdentityRole): Promise<void> {
  if (role.built_in || isRoleOperating(role.id)) return;
  removingRoleId.value = role.id;
  try { await deleteIdentityRole(role.id); await loadRoles(); }
  catch (error) { errorMessage.value = error instanceof ApiError ? error.message : '删除角色失败'; }
  finally { removingRoleId.value = null; }
}
async function openPermissions(role: IdentityRole): Promise<void> {
  if (isRoleOperating(role.id)) return;
  permissionRole.value = role;
  permissionCode.value = '';
  permissionScope.value = role.scope_type === 'project' ? 'project' : 'unit';
  permissionError.value = '';
  permissionOpen.value = true;
  permissionController?.abort(); permissionController = new AbortController(); permissionLoading.value = true;
  try { permissions.value = await listIdentityPermissions(permissionController.signal); }
  catch (error) { if (error instanceof DOMException && error.name === 'AbortError') return; permissionError.value = error instanceof ApiError ? error.message : '权限目录加载失败'; }
  finally { permissionLoading.value = false; }
}
function closePermissions(): void { permissionController?.abort(); permissionOpen.value = false; permissionRole.value = null; }
async function submitPermissionGrant(): Promise<void> {
  if (!permissionRole.value || !permissionCode.value || granting.value) return;
  granting.value = true; permissionError.value = '';
  try {
    await grantIdentityRolePermission(permissionRole.value.id, { permission_code: permissionCode.value, data_scope: permissionScope.value });
    closePermissions();
  } catch (error) { permissionError.value = error instanceof ApiError ? error.message : '权限授权失败'; }
  finally { granting.value = false; }
}
async function loadRoles(): Promise<void> {
  controller?.abort(); controller = new AbortController(); loading.value = true; errorMessage.value = '';
  try { roles.value = await listIdentityRoles(controller.signal); }
  catch (error) { if (error instanceof DOMException && error.name === 'AbortError') return; errorMessage.value = error instanceof ApiError ? error.message : '角色数据加载失败'; }
  finally { loading.value = false; }
}
onMounted(loadRoles); onBeforeUnmount(() => { controller?.abort(); permissionController?.abort(); });
</script>

<style scoped>
.disabled-action { color: rgba(0, 0, 0, 0.25); cursor: not-allowed; pointer-events: none; }
</style>
