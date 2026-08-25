<template>
  <div class="page-grid">
    <a-card class="section-card">
      <div class="toolbar">
        <div>
          <a-typography-title :level="4" style="margin: 0">用户与权限</a-typography-title>
          <a-typography-text type="secondary">维护当前单位的用户、成员状态和授权关系。</a-typography-text>
        </div>
        <a-space>
          <a-button :loading="loading" @click="loadUsers">刷新</a-button>
          <a-button type="primary" @click="openCreate">新建用户</a-button>
        </a-space>
      </div>

      <a-row :gutter="16">
        <a-col v-for="metric in metrics" :key="metric.label" :span="8">
          <a-card class="metric-card">
            <div class="metric-label">{{ metric.label }}</div>
            <div class="metric-value">{{ metric.value }}</div>
            <a-tag :color="metric.color">{{ metric.hint }}</a-tag>
          </a-card>
        </a-col>
      </a-row>
    </a-card>

    <a-card class="section-card" title="用户列表">
      <a-alert
        v-if="errorMessage"
        type="error"
        show-icon
        :message="errorMessage"
        style="margin-bottom: 16px"
      />
      <a-table
        :columns="columns"
        :data-source="users"
        :loading="loading"
        row-key="id"
        :pagination="{ pageSize: 10 }"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'email'">{{ record.email || '-' }}</template>
          <a-tag v-else-if="column.key === 'status'" :color="statusColor(record.status)">
            {{ statusText(record.status) }}
          </a-tag>
          <a-tag v-else-if="column.key === 'membership_status'" :color="statusColor(record.membership_status)">
            {{ statusText(record.membership_status) }}
          </a-tag>
          <a-space v-else-if="column.key === 'action'">
            <a @click="openEdit(record)">编辑</a>
            <a v-if="!isOidcUser(record)" @click="openReset(record)">重置密码</a>
            <a-popconfirm title="确认删除该用户？" @confirm="deleteUser(record)"><a>删除</a></a-popconfirm>
            <a @click="openRoles(record)">角色管理</a>
            <a-popconfirm title="确认切换用户状态？" @confirm="toggleStatus(record)">
              <a :class="{ 'disabled-action': savingId === record.id }">{{ record.status === 'active' ? '停用' : '启用' }}</a>
            </a-popconfirm>
          </a-space>
        </template>
        <template #emptyText>
          <a-empty description="当前单位暂无用户数据" />
        </template>
      </a-table>
    </a-card>
    <a-modal v-model:open="createOpen" title="新建用户" :confirm-loading="creating" @ok="submitCreate">
      <a-form layout="vertical">
        <a-form-item label="用户名称"><a-input v-model:value="createForm.display_name" /></a-form-item>
        <a-form-item label="邮箱"><a-input v-model:value="createForm.email" /></a-form-item>
        <a-form-item label="初始密码">
          <a-input-password v-model:value="createForm.initial_password" />
          <a-button @click="generateCreatePassword">随机生成</a-button>
          <a-button @click="copyCreatePassword">复制</a-button>
        </a-form-item>
        <a-form-item label="单位角色" required>
          <a-select
            v-model:value="createForm.role_ids"
            mode="multiple"
            :loading="accountRolesLoading"
            :options="unitRoleOptions"
            placeholder="请选择至少一个单位角色"
          />
        </a-form-item>
      </a-form>
    </a-modal>
    <a-modal v-model:open="editOpen" title="编辑用户" :confirm-loading="editing" @ok="submitEdit">
      <a-form layout="vertical">
        <a-form-item label="用户名称"><a-input v-model:value="editForm.display_name" /></a-form-item>
        <a-form-item label="邮箱"><a-input v-model:value="editForm.email" /></a-form-item>
        <a-form-item label="单位角色" required>
          <a-select
            v-model:value="editForm.role_ids"
            mode="multiple"
            :loading="accountRolesLoading"
            :options="unitRoleOptions"
            placeholder="请选择至少一个单位角色"
          />
        </a-form-item>
      </a-form>
    </a-modal>
    <a-modal v-model:open="resetOpen" title="重置密码" :confirm-loading="resetting" @ok="submitReset">
      <a-form layout="vertical">
        <a-form-item label="随机密码"><a-input-password v-model:value="resetForm.new_password" readonly /><a-button @click="generateResetPassword">重新生成</a-button><a-button @click="copyResetPassword">复制</a-button></a-form-item>
      </a-form>
    </a-modal>
    <a-modal v-model:open="rolesOpen" title="角色管理" :confirm-loading="rolesSaving || rolesLoading" @ok="submitRoles">
      <a-form layout="vertical">
        <a-form-item label="授权范围">
          <a-select v-model:value="roleScope" :options="scopeOptions" @change="onRoleScopeChange" />
        </a-form-item>
        <a-form-item v-if="roleScope === 'project'" label="项目">
          <a-select v-model:value="roleProjectId" :options="projectOptions" @change="loadRoleBindings" />
        </a-form-item>
        <a-form-item label="角色">
          <a-select v-model:value="selectedRoleIds" mode="multiple" :options="roleOptions" />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { message } from 'ant-design-vue';
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';

import { ApiError } from '@/api/client';
import {
  createIdentityUser,
  deleteIdentityUser,
  listIdentityProjects,
  listIdentityRoles,
  listIdentityUserRoles,
  listIdentityUsers,
  replaceIdentityUserRoles,
  resetIdentityUserPassword,
  setIdentityUserStatus,
  updateIdentityUser,
  type IdentityProject,
  type IdentityRole,
  type IdentityUser,
} from '@/api/identity';
import { copyText } from '@/utils/clipboard';

const users = ref<IdentityUser[]>([]);
const loading = ref(false);
const errorMessage = ref('');
let controller: AbortController | null = null;
let roleController: AbortController | null = null;
let accountRoleController: AbortController | null = null;
const createOpen = ref(false);
const creating = ref(false);
const savingId = ref<string | null>(null);
const accountRolesLoading = ref(false);
const createForm = ref({ display_name: '', email: '', initial_password: '', role_ids: [] as string[] });
const editOpen = ref(false);
const editing = ref(false);
const editUserId = ref('');
const editForm = ref({ display_name: '', email: '', role_ids: [] as string[] });
const resetOpen = ref(false);
const resetting = ref(false);
const resetUserId = ref('');
const resetForm = ref({ new_password: '' });
const rolesOpen = ref(false);
const rolesSaving = ref(false);
const rolesLoading = ref(false);
const roleUserId = ref('');
const roleScope = ref<'unit' | 'project'>('unit');
const roleProjectId = ref<string | null>(null);
const selectedRoleIds = ref<string[]>([]);
const availableRoles = ref<IdentityRole[]>([]);
const projects = ref<IdentityProject[]>([]);
const scopeOptions = [
  { label: '单位角色', value: 'unit' },
  { label: '项目角色', value: 'project' },
];

const roleOptions = computed(() => availableRoles.value
  .filter((role) => role.status === 'active' && role.scope_type === roleScope.value)
  .map((role) => ({ label: `${role.name} (${role.code})`, value: role.id })));
const unitRoleOptions = computed(() => availableRoles.value
  .filter((role) => role.status === 'active' && role.scope_type === 'unit')
  .map((role) => ({ label: `${role.name} (${role.code})`, value: role.id })));
const projectOptions = computed(() => projects.value
  .filter((project) => project.status === 'active')
  .map((project) => ({ label: `${project.name} (${project.code})`, value: project.id })));

const metrics = computed(() => [
  { label: '用户数', value: users.value.length, hint: '当前单位', color: 'blue' },
  { label: '启用账号', value: users.value.filter((item) => item.status === 'active').length, hint: '可登录', color: 'green' },
  { label: '停用账号', value: users.value.filter((item) => item.status !== 'active').length, hint: '已限制', color: 'red' },
]);

const columns = [
  { title: '用户名称', dataIndex: 'display_name' },
  { title: '邮箱', key: 'email', dataIndex: 'email' },
  { title: '账号状态', key: 'status', width: 120 },
  { title: '单位成员状态', key: 'membership_status', width: 140 },
  { title: '操作', key: 'action', width: 260 },
];

function statusText(status: string): string {
  return status === 'active' ? '启用' : '停用';
}

function statusColor(status: string): string {
  return status === 'active' ? 'green' : 'default';
}

async function loadCreateUnitRoles(): Promise<void> {
  accountRoleController?.abort();
  accountRoleController = new AbortController();
  accountRolesLoading.value = true;
  try {
    availableRoles.value = await listIdentityRoles(accountRoleController.signal);
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') return;
    errorMessage.value = error instanceof ApiError ? error.message : '单位角色加载失败';
  } finally {
    accountRolesLoading.value = false;
  }
}

function openCreate(): void {
  createForm.value = { display_name: '', email: '', initial_password: randomPassword(), role_ids: [] };
  createOpen.value = true;
  void loadCreateUnitRoles();
}

async function openEdit(user: IdentityUser): Promise<void> {
  editUserId.value = user.id;
  editForm.value = { display_name: user.display_name, email: user.email || '', role_ids: [] };
  editOpen.value = true;
  accountRoleController?.abort();
  const currentController = new AbortController();
  accountRoleController = currentController;
  accountRolesLoading.value = true;
  try {
    const [roles, current] = await Promise.all([
      listIdentityRoles(currentController.signal),
      listIdentityUserRoles(user.id, null, currentController.signal),
    ]);
    if (accountRoleController !== currentController) return;
    availableRoles.value = roles;
    const activeUnitRoleIds = new Set(roles
      .filter((role) => role.status === 'active' && role.scope_type === 'unit')
      .map((role) => role.id));
    editForm.value.role_ids = current
      .filter((role) => role.scope_type === 'unit' && activeUnitRoleIds.has(role.role_id))
      .map((role) => role.role_id);
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') return;
    if (accountRoleController !== currentController) return;
    errorMessage.value = error instanceof ApiError ? error.message : '单位角色加载失败';
  } finally {
    if (accountRoleController === currentController) accountRolesLoading.value = false;
  }
}
function isOidcUser(user: IdentityUser): boolean { return user.auth_method === 'oidc' || user.external_identity === true; }
function randomPassword(): string {
  const sets = ['ABCDEFGHJKLMNPQRSTUVWXYZ', 'abcdefghijkmnopqrstuvwxyz', '23456789', '!@#$%^&*'];
  const pick = (chars: string) => chars[crypto.getRandomValues(new Uint32Array(1))[0] % chars.length];
  const chars = sets.map(pick);
  const all = sets.join('');
  while (chars.length < 16) chars.push(pick(all));
  return chars.sort(() => Math.random() - 0.5).join('');
}
function openReset(user: IdentityUser): void { resetUserId.value = user.id; resetForm.value = { new_password: randomPassword() }; resetOpen.value = true; }
function generateCreatePassword(): void { createForm.value.initial_password = randomPassword(); }
function generateResetPassword(): void { resetForm.value.new_password = randomPassword(); }
async function copyPassword(password: string): Promise<void> {
  if (!password) return;
  try {
    await copyText(password);
    message.success('密码已复制');
  } catch {
    message.error('复制失败，请手动选择密码复制');
  }
}
async function copyCreatePassword(): Promise<void> { await copyPassword(createForm.value.initial_password); }
async function copyResetPassword(): Promise<void> { await copyPassword(resetForm.value.new_password); }
async function deleteUser(user: IdentityUser): Promise<void> { try { await deleteIdentityUser(user.id); await loadUsers(); } catch (error) { errorMessage.value = error instanceof ApiError ? error.message : '删除用户失败'; } }
function openRoles(user: IdentityUser): void {
  roleUserId.value = user.id;
  roleScope.value = 'unit';
  roleProjectId.value = null;
  selectedRoleIds.value = [];
  rolesOpen.value = true;
  void loadRoleBindings();
}

async function submitCreate(): Promise<void> {
  if (!createForm.value.display_name.trim()) return;
  if (!createForm.value.email.trim()) return message.error('请输入邮箱');
  if (createForm.value.initial_password.length < 12) return message.error('初始密码至少需要 12 位');
  if (!createForm.value.role_ids.length) return message.error('请至少选择一个单位角色');
  creating.value = true;
  try {
    await createIdentityUser({
      display_name: createForm.value.display_name.trim(),
      email: createForm.value.email.trim(),
      initial_password: createForm.value.initial_password,
      role_ids: createForm.value.role_ids,
    });
    createOpen.value = false;
    createForm.value = { display_name: '', email: '', initial_password: '', role_ids: [] };
    await loadUsers();
  }
  catch (error) { errorMessage.value = error instanceof ApiError ? error.message : '创建用户失败'; }
  finally { creating.value = false; }
}

async function toggleStatus(user: IdentityUser): Promise<void> {
  savingId.value = user.id;
  try { await setIdentityUserStatus(user.id, user.status === 'active' ? 'inactive' : 'active'); await loadUsers(); }
  catch (error) { errorMessage.value = error instanceof ApiError ? error.message : '更新用户状态失败'; }
  finally { savingId.value = null; }
}
async function submitEdit(): Promise<void> {
  if (!editForm.value.display_name.trim() || !editUserId.value) return;
  if (!editForm.value.role_ids.length) return message.error('请至少选择一个单位角色');
  editing.value = true;
  try {
    await updateIdentityUser(editUserId.value, {
      display_name: editForm.value.display_name.trim(),
      email: editForm.value.email || null,
      role_ids: editForm.value.role_ids,
    });
    editOpen.value = false;
    await loadUsers();
  }
  catch (error) { errorMessage.value = error instanceof ApiError ? error.message : '编辑用户失败'; }
  finally { editing.value = false; }
}

async function submitReset(): Promise<void> {
  if (!resetUserId.value || resetForm.value.new_password.length < 12) return;
  resetting.value = true;
  try {
    await resetIdentityUserPassword(resetUserId.value, { new_password: resetForm.value.new_password });
    resetOpen.value = false;
    resetForm.value = { new_password: '' };
    await loadUsers();
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '重置密码失败';
  } finally {
    resetting.value = false;
  }
}

async function onRoleScopeChange(): Promise<void> {
  if (roleScope.value === 'project' && !projects.value.length) {
    try {
      projects.value = await listIdentityProjects();
    } catch (error) {
      errorMessage.value = error instanceof ApiError ? error.message : '项目数据加载失败';
      return;
    }
  }
  roleProjectId.value = roleScope.value === 'project' ? (projects.value[0]?.id ?? null) : null;
  await loadRoleBindings();
}

async function loadRoleBindings(): Promise<void> {
  if (!roleUserId.value || (roleScope.value === 'project' && !roleProjectId.value)) return;
  roleController?.abort();
  roleController = new AbortController();
  rolesLoading.value = true;
  try {
    const [roles, current] = await Promise.all([
      availableRoles.value.length ? Promise.resolve(availableRoles.value) : listIdentityRoles(roleController.signal),
      listIdentityUserRoles(roleUserId.value, roleProjectId.value, roleController.signal),
    ]);
    availableRoles.value = roles;
    selectedRoleIds.value = current.map((role) => role.role_id);
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '角色数据加载失败';
  } finally {
    rolesLoading.value = false;
  }
}

async function submitRoles(): Promise<void> {
  if (!roleUserId.value || (roleScope.value === 'project' && !roleProjectId.value)) return;
  rolesSaving.value = true;
  try {
    await replaceIdentityUserRoles(roleUserId.value, {
      role_ids: selectedRoleIds.value,
      project_id: roleProjectId.value,
    });
    rolesOpen.value = false;
    await loadUsers();
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '保存角色失败';
  } finally {
    rolesSaving.value = false;
  }
}

async function loadUsers(): Promise<void> {
  controller?.abort();
  controller = new AbortController();
  loading.value = true;
  errorMessage.value = '';
  try {
    users.value = await listIdentityUsers(controller.signal);
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') return;
    errorMessage.value = error instanceof ApiError ? error.message : '用户数据加载失败';
  } finally {
    loading.value = false;
  }
}

onMounted(loadUsers);
onBeforeUnmount(() => { controller?.abort(); roleController?.abort(); accountRoleController?.abort(); });
</script>

<style scoped>
.disabled-action {
  color: rgba(0, 0, 0, 0.25);
  cursor: not-allowed;
  pointer-events: none;
}
</style>
