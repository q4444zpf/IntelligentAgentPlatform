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
          <a-button type="primary" disabled>新建用户</a-button>
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
            <a class="disabled-action">查看</a>
            <a class="disabled-action">授权</a>
          </a-space>
        </template>
        <template #emptyText>
          <a-empty description="当前单位暂无用户数据" />
        </template>
      </a-table>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';

import { ApiError } from '@/api/client';
import { listIdentityUsers, type IdentityUser } from '@/api/identity';

const users = ref<IdentityUser[]>([]);
const loading = ref(false);
const errorMessage = ref('');
let controller: AbortController | null = null;

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
  { title: '操作', key: 'action', width: 130 },
];

function statusText(status: string): string {
  return status === 'active' ? '启用' : '停用';
}

function statusColor(status: string): string {
  return status === 'active' ? 'green' : 'default';
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
onBeforeUnmount(() => controller?.abort());
</script>

<style scoped>
.disabled-action {
  color: rgba(0, 0, 0, 0.25);
  cursor: not-allowed;
  pointer-events: none;
}
</style>
