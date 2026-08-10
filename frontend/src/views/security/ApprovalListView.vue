<template>
  <div class="page-grid approval-page">
    <a-card class="section-card">
      <div class="toolbar">
        <div>
          <a-typography-title :level="4" style="margin: 0">待办审批</a-typography-title>
          <a-typography-text type="secondary">查看高风险工具调用，核对参数后批准或拒绝。</a-typography-text>
        </div>
        <a-space>
          <a-select v-model:value="status" :options="statusOptions" aria-label="审批状态" @change="loadApprovals" />
          <a-button :loading="loading" aria-label="刷新审批列表" @click="loadApprovals">刷新</a-button>
        </a-space>
      </div>
    </a-card>

    <a-card class="section-card" title="审批单">
      <a-alert v-if="errorMessage" type="error" show-icon :message="errorMessage" style="margin-bottom: 16px" />
      <a-table :columns="columns" :data-source="approvals" :loading="loading" row-key="id" :pagination="false">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'risk'">
            <a-tag :color="riskColor(record.risk_level)">{{ riskText(record.risk_level) }}</a-tag>
          </template>
          <template v-else-if="column.key === 'arguments'">
            <code class="argument-summary">{{ formatArguments(record.arguments_summary) }}</code>
          </template>
          <template v-else-if="column.key === 'expires_at'">{{ formatTime(record.expires_at) }}</template>
          <template v-else-if="column.key === 'status'"><a-tag>{{ statusText(record.status) }}</a-tag></template>
          <template v-else-if="column.key === 'action' && record.status === 'pending'">
            <a-space>
              <a-button type="primary" size="small" :loading="loadingId === record.id" :disabled="actionBusy" :aria-label="`批准审批 ${record.id}`" @click="decide(record, 'approve')">批准</a-button>
              <a-button danger size="small" :loading="loadingId === record.id" :disabled="actionBusy" :aria-label="`拒绝审批 ${record.id}`" @click="decide(record, 'reject')">拒绝</a-button>
            </a-space>
          </template>
        </template>
        <template #emptyText><a-empty description="暂无待办审批" /></template>
      </a-table>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';

import { ApiError } from '@/api/client';
import { approvalsApi, type Approval } from '@/api/approvals';

const approvals = ref<Approval[]>([]);
const status = ref<'pending' | 'all'>('pending');
const loading = ref(false);
const loadingId = ref<string | null>(null);
const errorMessage = ref('');
let controller: AbortController | null = null;

const statusOptions = [{ label: '待审批', value: 'pending' }, { label: '全部记录', value: 'all' }];
const columns = [
  { title: '工具', dataIndex: 'tool_id', key: 'tool_id' },
  { title: '风险', key: 'risk', width: 90 },
  { title: '参数摘要', key: 'arguments' },
  { title: '申请人', dataIndex: 'requester_id', width: 120 },
  { title: '截止时间', key: 'expires_at', width: 180 },
  { title: '状态', key: 'status', width: 100 },
  { title: '操作', key: 'action', width: 150 },
];
const actionBusy = computed(() => loadingId.value !== null);

function riskText(value: string): string { return ({ critical: '严重风险', high: '高风险', medium: '中风险', low: '低风险' }[value] || value); }
function riskColor(value: string): string { return ({ critical: 'red', high: 'orange', medium: 'gold', low: 'green' }[value] || 'default'); }
function statusText(value: Approval['status']): string { return ({ pending: '待审批', approved: '已批准', rejected: '已拒绝', expired: '已过期', cancelled: '已取消' }[value]); }
function formatArguments(value: Record<string, unknown>): string { return JSON.stringify(value); }
function formatTime(value: string): string { const date = new Date(value); return Number.isNaN(date.getTime()) ? '-' : date.toLocaleString('zh-CN'); }
function errorText(error: unknown): string { return error instanceof ApiError || error instanceof Error ? error.message : '审批请求失败'; }

async function loadApprovals(): Promise<void> {
  controller?.abort();
  const current = new AbortController();
  controller = current;
  loading.value = true;
  errorMessage.value = '';
  try {
    const result = await approvalsApi.list(status.value, current.signal);
    if (!current.signal.aborted) approvals.value = result;
  } catch (error) {
    if (!(error instanceof DOMException && error.name === 'AbortError')) errorMessage.value = errorText(error);
  } finally {
    if (controller === current) { controller = null; loading.value = false; }
  }
}

async function decide(approval: Approval, decision: 'approve' | 'reject'): Promise<void> {
  if (loadingId.value) return;
  loadingId.value = approval.id;
  errorMessage.value = '';
  try {
    if (decision === 'approve') await approvalsApi.approve(approval.id);
    else await approvalsApi.reject(approval.id);
    await loadApprovals();
  } catch (error) { errorMessage.value = errorText(error); }
  finally { loadingId.value = null; }
}

onMounted(loadApprovals);
onBeforeUnmount(() => controller?.abort());
</script>

<style scoped>
.approval-page { min-width: 0; }
.argument-summary { display: inline-block; max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; vertical-align: middle; }
.section-card :deep(.ant-table-cell) { vertical-align: middle; }
@media (max-width: 760px) { .approval-page :deep(.toolbar) { align-items: flex-start; flex-direction: column; } .approval-page :deep(.ant-table) { min-width: 860px; } }
</style>
