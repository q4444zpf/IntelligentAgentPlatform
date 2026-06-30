<template>
  <div class="page-grid">
    <div class="metric-grid">
      <a-card v-for="item in metrics" :key="item.label" class="metric-card">
        <div class="metric-label">{{ item.label }}</div>
        <div class="metric-value">{{ item.value }}</div>
        <a-tag :color="item.color">{{ item.hint }}</a-tag>
      </a-card>
    </div>

    <a-card class="section-card" :title="pageTitle">
      <div class="toolbar">
        <a-space>
          <a-input-search placeholder="搜索智能体、MCP、Skill、流程" style="width: 320px" />
          <a-select value="all" style="width: 140px" :options="[{ label: '全部类型', value: 'all' }]" />
        </a-space>
        <a-space>
          <a-button v-if="!isPublic">提交公用发布</a-button>
          <a-button v-if="isReview && permissionStore.isAdmin">批量审核</a-button>
          <a-button type="primary">{{ primaryActionText }}</a-button>
        </a-space>
      </div>

      <a-table :columns="columns" :data-source="resources" row-key="id">
        <template #bodyCell="{ column, record }">
          <a-tag v-if="column.key === 'type'" :color="resourceColor[record.type]">
            {{ resourceTypeText[record.type] }}
          </a-tag>
          <a-tag v-if="column.key === 'status'" :color="publishColor[record.status]">
            {{ publishStatusText[record.status] }}
          </a-tag>
          <a-space v-if="column.key === 'action'">
            <a>查看</a>
            <a>{{ actionText }}</a>
            <a v-if="!isPublic">发布</a>
            <a v-if="isPublic && permissionStore.isAdmin">下架</a>
            <a v-if="isReview && permissionStore.isAdmin">审核</a>
          </a-space>
        </template>
      </a-table>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute } from 'vue-router';

import { getPersonalResources, getPublicResources } from '@/mock/api';
import { usePermissionStore } from '@/stores/permission';
import type { PlatformResource } from '@/types/platform';

const route = useRoute();
const permissionStore = usePermissionStore();
const resources = ref<PlatformResource[]>([]);
const isPublic = computed(() => route.path.includes('/public'));
const isReview = computed(() => route.path.includes('/review'));
const routeType = computed(() => {
  if (route.path.includes('/agents')) return 'agent';
  if (route.path.includes('/mcp')) return 'mcp';
  if (route.path.includes('/skills')) return 'skill';
  if (route.path.includes('/publish') || route.path.includes('/review')) return 'pending';
  return 'all';
});

const pageTitle = computed(() => String(route.meta.title || (isPublic.value ? '公用资源库' : '个人资源空间')));
const primaryActionText = computed(() => {
  if (isReview.value) return '审核规则配置';
  if (isPublic.value) return permissionStore.isAdmin ? '新增公用资源' : '复制到个人空间';
  if (routeType.value === 'agent') return '新建智能体';
  if (routeType.value === 'mcp') return '新建 MCP';
  if (routeType.value === 'skill') return '新建 Skill';
  return '新建资源';
});
const actionText = computed(() => {
  if (isReview.value) return '详情';
  if (isPublic.value) return permissionStore.isAdmin ? '管理' : '复制';
  return '编辑';
});

const resourceTypeText: Record<string, string> = {
  agent: '智能体',
  mcp: 'MCP',
  skill: 'Skill',
  workflow: '流程',
};
const resourceColor: Record<string, string> = {
  agent: 'blue',
  mcp: 'purple',
  skill: 'green',
  workflow: 'cyan',
};
const publishStatusText: Record<string, string> = {
  private_active: '私有可用',
  pending_review: '待审核',
  public_active: '公用已发布',
  rejected: '已驳回',
};
const publishColor: Record<string, string> = {
  private_active: 'blue',
  pending_review: 'gold',
  public_active: 'green',
  rejected: 'red',
};

const metrics = computed(() => [
  { label: '资源总数', value: resources.value.length, hint: isPublic.value ? '平台共享' : '个人维护', color: 'blue' },
  { label: '智能体', value: resources.value.filter((item) => item.type === 'agent').length, hint: '可直接对话', color: 'cyan' },
  { label: 'MCP / Skill', value: resources.value.filter((item) => ['mcp', 'skill'].includes(item.type)).length, hint: '工具能力', color: 'green' },
  { label: '待审核', value: resources.value.filter((item) => item.status === 'pending_review').length, hint: '发布流程', color: 'gold' },
]);

const columns = [
  { title: '资源名称', dataIndex: 'name' },
  { title: '类型', key: 'type', width: 100 },
  { title: '说明', dataIndex: 'description' },
  { title: '所有者', dataIndex: 'owner', width: 150 },
  { title: '调用量', dataIndex: 'calls', width: 100 },
  { title: '发布状态', key: 'status', width: 120 },
  { title: '更新时间', dataIndex: 'updatedAt', width: 170 },
  { title: '操作', key: 'action', width: 150 },
];

async function loadData() {
  const source = isReview.value ? await getPersonalResources() : isPublic.value ? await getPublicResources() : await getPersonalResources();
  resources.value = source.filter((item) => {
    if (routeType.value === 'pending') return item.status === 'pending_review';
    if (routeType.value === 'all') return true;
    return item.type === routeType.value;
  });
}

onMounted(loadData);
watch(() => route.fullPath, loadData);
</script>
