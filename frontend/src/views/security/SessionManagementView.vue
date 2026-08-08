<template>
  <div class="page-grid session-page">
    <a-card class="section-card">
      <div class="toolbar">
        <div>
          <a-typography-title :level="4" style="margin: 0">会话管理</a-typography-title>
          <a-typography-text type="secondary">查看当前账号的登录会话，并撤销不再使用的设备。</a-typography-text>
        </div>
        <a-space>
          <a-button :loading="loading" aria-label="刷新会话列表" @click="loadSessions">刷新</a-button>
          <a-button :loading="revokeOthersLoading" :disabled="!hasOtherSessions" aria-label="撤销其他会话" @click="revokeOthers">撤销其他会话</a-button>
        </a-space>
      </div>
    </a-card>

    <a-card class="section-card" title="登录会话">
      <a-alert v-if="errorMessage" type="error" show-icon :message="errorMessage" style="margin-bottom: 16px" />
      <a-table :columns="columns" :data-source="sessions" :loading="loading" row-key="session_id" :pagination="false">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'session_id'">
            <span class="session-id" :title="maskSessionId(record.session_id)">{{ maskSessionId(record.session_id) }}</span>
            <a-tag v-if="record.is_current_session" color="blue">当前会话</a-tag>
          </template>
          <template v-else-if="column.key === 'auth_method'">{{ authMethodText(record.auth_method) }}</template>
          <template v-else-if="column.key === 'project'">{{ record.current_project?.name || '未选择项目' }}</template>
          <template v-else-if="column.key === 'last_seen_at'">{{ formatTime(record.last_seen_at) }}</template>
          <template v-else-if="column.key === 'action'">
            <a-popconfirm title="撤销后该会话将立即失效，是否继续？" ok-text="确认撤销" cancel-text="取消" @confirm="revokeSession(record)">
              <a-button type="link" danger :loading="revokeLoadingId === record.session_id" :disabled="isRevoking(record.session_id)">撤销</a-button>
            </a-popconfirm>
          </template>
        </template>
        <template #emptyText><a-empty description="暂无有效会话" /></template>
      </a-table>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { ApiError } from '@/api/client';
import { sessionApi, type AuthSessionSummary } from '@/api/auth';

const router = useRouter();
const sessions = ref<AuthSessionSummary[]>([]);
const loading = ref(false);
const errorMessage = ref('');
const revokeLoadingId = ref<string | null>(null);
const revokeOthersLoading = ref(false);
let controller: AbortController | null = null;

const columns = [
  { title: '会话', key: 'session_id' },
  { title: '认证方式', key: 'auth_method' },
  { title: '当前项目', key: 'project' },
  { title: '最近活动', key: 'last_seen_at' },
  { title: '操作', key: 'action' },
];

const hasOtherSessions = computed(() => sessions.value.some((item) => !item.is_current_session));
const isAbort = (error: unknown) => error instanceof DOMException && error.name === 'AbortError';
const errorText = (error: unknown, fallback: string) => error instanceof ApiError || error instanceof Error ? error.message : fallback;

function maskSessionId(sessionId: string): string {
  if (sessionId.length <= 10) return `${sessionId.slice(0, 3)}...`;
  return `${sessionId.slice(0, 8)}...${sessionId.slice(-4)}`;
}

function authMethodText(method: string): string {
  if (method === 'oidc') return '统一认证';
  if (method === 'local') return '本地账号';
  if (method === 'dev_test') return '开发身份';
  return method || '未知';
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleString('zh-CN');
}

function isRevoking(sessionId: string): boolean {
  return revokeLoadingId.value !== null && revokeLoadingId.value !== sessionId;
}

async function loadSessions(): Promise<void> {
  controller?.abort();
  const currentController = new AbortController();
  controller = currentController;
  loading.value = true;
  errorMessage.value = '';
  try {
    const result = await sessionApi.list(currentController.signal);
    if (!currentController.signal.aborted) sessions.value = result.sessions;
  } catch (error) {
    if (!isAbort(error)) errorMessage.value = errorText(error, '会话列表加载失败');
  } finally {
    if (controller === currentController) {
      controller = null;
      loading.value = false;
    }
  }
}

async function revokeSession(target: AuthSessionSummary): Promise<void> {
  if (revokeLoadingId.value || revokeOthersLoading.value) return;
  revokeLoadingId.value = target.session_id;
  errorMessage.value = '';
  try {
    await sessionApi.revoke(target.session_id);
    if (target.is_current_session) {
      await router.replace('/login');
      return;
    }
    await loadSessions();
  } catch (error) {
    errorMessage.value = errorText(error, '会话撤销失败');
  } finally {
    revokeLoadingId.value = null;
  }
}

async function revokeOthers(): Promise<void> {
  if (revokeOthersLoading.value || !hasOtherSessions.value) return;
  revokeOthersLoading.value = true;
  errorMessage.value = '';
  try {
    await sessionApi.revokeOthers();
    await loadSessions();
  } catch (error) {
    errorMessage.value = errorText(error, '其他会话撤销失败');
  } finally {
    revokeOthersLoading.value = false;
  }
}

onMounted(loadSessions);
onBeforeUnmount(() => controller?.abort());
</script>

<style scoped>
.session-page { min-width: 0; }
.session-id { display: inline-block; margin-right: 8px; font-family: Consolas, monospace; }
.section-card :deep(.ant-table-cell) { vertical-align: middle; }
@media (max-width: 760px) {
  .session-page :deep(.toolbar) { align-items: flex-start; flex-direction: column; }
  .session-page :deep(.ant-table) { min-width: 680px; }
}
</style>
