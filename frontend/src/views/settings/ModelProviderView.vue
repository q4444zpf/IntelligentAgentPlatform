<template>
  <a-card class="section-card" :title="permissionStore.isAdmin ? '平台大模型供应商与自定义接口' : '我的大模型配置'">
    <a-alert
      show-icon
      :type="permissionStore.isAdmin ? 'info' : 'success'"
      :message="permissionStore.isAdmin ? '管理员可维护公共模型供应商和全局默认模型' : '普通用户可维护自己的模型 API Key、自定义 OpenAI-compatible 接口和默认模型'"
      style="margin-bottom: 16px"
    />
    <div class="toolbar">
      <a-space>
        <a-input-search placeholder="搜索供应商、模型网关" style="width: 300px" />
        <a-select value="all" style="width: 160px" :options="[{ label: '全部供应商', value: 'all' }]" />
      </a-space>
      <a-space>
        <a-button>测试连接</a-button>
        <a-button type="primary">{{ permissionStore.isAdmin ? '新增供应商' : '新增我的模型' }}</a-button>
      </a-space>
    </div>

    <a-table :columns="columns" :data-source="providers" row-key="id">
      <template #bodyCell="{ column, record }">
        <a-tag v-if="column.key === 'region'" :color="regionColor[record.region]">
          {{ regionText[record.region] }}
        </a-tag>
        <a-tag v-if="column.key === 'protocol'" color="blue">{{ record.protocol }}</a-tag>
        <template v-if="column.key === 'modelTypes'">
          <a-space wrap>
            <a-tag v-for="type in record.modelTypes" :key="type">{{ type }}</a-tag>
          </a-space>
        </template>
        <a-badge v-if="column.key === 'status'" :status="record.status === 'enabled' ? 'success' : 'default'" :text="record.status === 'enabled' ? '启用' : '停用'" />
        <a-space v-if="column.key === 'action'">
          <a>配置</a>
          <a>测试</a>
          <a>绑定</a>
        </a-space>
      </template>
    </a-table>
  </a-card>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';

import { getModelProviders } from '@/mock/api';
import { usePermissionStore } from '@/stores/permission';
import type { ModelProvider } from '@/types/platform';

const providers = ref<ModelProvider[]>([]);
const permissionStore = usePermissionStore();
const regionText: Record<string, string> = {
  domestic: '国内',
  global: '国外',
  private: '私有化',
  custom: '自定义',
};
const regionColor: Record<string, string> = {
  domestic: 'red',
  global: 'blue',
  private: 'purple',
  custom: 'cyan',
};
const columns = [
  { title: '供应商', dataIndex: 'name' },
  { title: '类型', key: 'region', width: 100 },
  { title: '协议', key: 'protocol', width: 170 },
  { title: '模型能力', key: 'modelTypes', width: 260 },
  { title: 'API Base URL', dataIndex: 'endpoint' },
  { title: '状态', key: 'status', width: 100 },
  { title: '操作', key: 'action', width: 140 },
];

onMounted(async () => {
  providers.value = await getModelProviders();
});
</script>
