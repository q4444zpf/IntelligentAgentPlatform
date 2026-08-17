<template>
  <div class="page-grid">
    <a-alert v-if="errorMessage" type="warning" show-icon :message="errorMessage" closable @close="errorMessage = ''" />

    <div class="dashboard-heading">
      <div>
        <a-typography-title :level="3">平台运行总览</a-typography-title>
        <a-typography-text type="secondary">集中查看资源、模型和安全执行状态</a-typography-text>
      </div>
    </div>

    <a-spin :spinning="loading && !overview">
    <div class="metric-grid">
      <a-card v-for="item in metrics" :key="item.label" class="metric-card">
        <div class="metric-label">{{ item.label }}</div>
        <div class="metric-value">{{ item.value }}</div>
        <a-tag :color="item.color">{{ item.hint }}</a-tag>
      </a-card>
    </div>
    </a-spin>

    <a-card class="section-card service-status-card">
      <template #title>基础服务状态</template>
      <template #extra>
        <a-space size="small">
          <a-typography-text v-if="services" type="secondary">{{ checkedAt }}</a-typography-text>
          <a-button size="small" :loading="servicesLoading" @click="refreshServices">
            <template #icon><ReloadOutlined /></template>
            刷新服务状态
          </a-button>
        </a-space>
      </template>
      <a-alert v-if="serviceError" type="warning" show-icon :message="serviceError" />
      <a-spin :spinning="servicesLoading && !services">
        <div class="service-status-grid">
          <div v-for="service in services?.services ?? []" :key="service.name" class="service-status-item">
            <div>
              <div class="service-name">{{ service.name }}</div>
              <a-typography-text type="secondary">{{ service.detail }}</a-typography-text>
            </div>
            <a-tag :color="serviceState(service.status).color">{{ serviceState(service.status).label }}</a-tag>
          </div>
        </div>
      </a-spin>
    </a-card>

    <a-row :gutter="16">
      <a-col :xs="24" :lg="14">
        <a-card class="section-card" title="平台核心闭环">
          <a-steps direction="vertical" :current="2" size="small" :items="steps" />
        </a-card>
      </a-col>
      <a-col :xs="24" :lg="10">
        <a-card class="section-card" title="待处理事项">
          <a-list :data-source="todos">
            <template #renderItem="{ item }">
              <a-list-item>
                <a-list-item-meta :title="item.title" :description="item.desc" />
                <a-tag :color="item.color">{{ item.status }}</a-tag>
              </a-list-item>
            </template>
          </a-list>
        </a-card>
      </a-col>
    </a-row>

    <a-row :gutter="16">
      <a-col :xs="24" :xl="12">
        <a-card class="section-card" title="资源运行概览">
          <a-table :columns="resourceColumns" :data-source="resourceRows" :pagination="false" row-key="name">
            <template #bodyCell="{ column, record }">
              <a-progress v-if="column.key === 'health'" :percent="record.health" size="small" />
              <a-tag v-if="column.key === 'status'" :color="record.color">{{ record.status }}</a-tag>
            </template>
          </a-table>
        </a-card>
      </a-col>
      <a-col :xs="24" :xl="12">
        <a-card class="section-card" title="安全与审计">
          <a-table :columns="auditColumns" :data-source="auditRows" :pagination="false" row-key="event">
            <template #bodyCell="{ column, record }">
              <a-tag v-if="column.key === 'level'" :color="record.color">{{ record.level }}</a-tag>
            </template>
          </a-table>
        </a-card>
      </a-col>
    </a-row>
  </div>
</template>

<script setup lang="ts">
import { ReloadOutlined } from '@ant-design/icons-vue';
import { computed, onMounted, onUnmounted, ref } from 'vue';

import { platformApi, type PlatformOverview, type PlatformServices, type ServiceStatus } from '@/api/platform';

const overview = ref<PlatformOverview | null>(null);
const loading = ref(false);
const errorMessage = ref('');
let controller: AbortController | undefined;
const services = ref<PlatformServices | null>(null);
const servicesLoading = ref(false);
const serviceError = ref('');
let servicesController: AbortController | undefined;
let servicesTimer: number | undefined;

const checkedAt = computed(() => (
  services.value ? new Date(services.value.checked_at).toLocaleString() : '尚未检查'
));

const metrics = computed(() => [
  { label: '智能体', value: 18, hint: '个人 / 公用 / 系统', color: 'blue' },
  { label: '已配置供应商', value: overview.value?.configured_provider_count ?? '-', hint: `共 ${overview.value?.provider_count ?? '-'} 个供应商`, color: 'green' },
  { label: '可用模型', value: overview.value?.enabled_model_count ?? '-', hint: `共 ${overview.value?.model_count ?? '-'} 个模型`, color: 'cyan' },
  { label: '流程实例', value: 126, hint: '今日运行', color: 'gold' },
]);

const steps = [
  { title: '创建个人资源', description: '用户维护自己的智能体、MCP、Skill、知识库和流程。' },
  { title: '调试与沙箱校验', description: 'Tool、MCP、脚本和文件处理任务进入 Sandbox Executor。' },
  { title: '提交公用发布', description: '系统自动校验 Schema、密钥、权限和高风险动作。' },
  { title: '审核通过并复用', description: '其他用户可直接使用或复制到个人空间。' },
];

const todos = [
  { title: '文档处理 MCP Server 发布审核', desc: '待确认网络白名单与沙箱策略', status: '待审核', color: 'gold' },
  { title: 'OpenAI-compatible 私有网关连通性测试', desc: '需完成 Chat / Embedding 测试', status: '待测试', color: 'blue' },
  { title: '工具调用审批流程', desc: '人工确认节点存在 3 个待办', status: '待处理', color: 'orange' },
];

const resourceColumns = [
  { title: '资源', dataIndex: 'name' },
  { title: '数量', dataIndex: 'count', width: 90 },
  { title: '健康度', key: 'health', width: 160 },
  { title: '状态', key: 'status', width: 110 },
];

const resourceRows = [
  { name: '智能体运行时', count: 18, health: 96, status: '正常', color: 'green' },
  { name: 'MCP Server', count: 9, health: 88, status: '关注', color: 'gold' },
  { name: 'Skill / Tool', count: 24, health: 93, status: '正常', color: 'green' },
  { name: '知识库索引', count: 12, health: 91, status: '正常', color: 'green' },
];

const auditColumns = [
  { title: '事件', dataIndex: 'event' },
  { title: '来源', dataIndex: 'source', width: 150 },
  { title: '等级', key: 'level', width: 100 },
  { title: '时间', dataIndex: 'time', width: 120 },
];

const auditRows = [
  { event: '阻断内网地址访问', source: 'Sandbox Executor', level: '高', color: 'red', time: '10:42' },
  { event: '提交公用资源发布', source: '个人资源空间', level: '中', color: 'orange', time: '10:12' },
  { event: '大模型调用成功', source: 'LLM Router', level: '低', color: 'green', time: '09:58' },
];

async function loadOverview() {
  controller?.abort();
  controller = new AbortController();
  loading.value = true;
  errorMessage.value = '';
  try {
    overview.value = await platformApi.overview(controller.signal);
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') return;
    errorMessage.value = error instanceof Error ? error.message : '平台状态读取失败';
  } finally {
    loading.value = false;
  }
}

function serviceState(status: ServiceStatus['status']) {
  if (status === 'healthy') return { label: '正常', color: 'green' };
  if (status === 'disabled') return { label: '未启用', color: 'default' };
  return { label: '异常', color: 'red' };
}

async function refreshServices() {
  servicesController?.abort();
  servicesController = new AbortController();
  servicesLoading.value = true;
  serviceError.value = '';
  try {
    services.value = await platformApi.services(servicesController.signal);
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') return;
    serviceError.value = error instanceof Error ? error.message : '基础服务状态读取失败';
  } finally {
    servicesLoading.value = false;
  }
}

onMounted(() => {
  loadOverview();
  refreshServices();
  servicesTimer = window.setInterval(refreshServices, 300000);
});
onUnmounted(() => {
  controller?.abort();
  servicesController?.abort();
  if (servicesTimer !== undefined) window.clearInterval(servicesTimer);
});
</script>

<style scoped>
.service-status-card {
  margin-top: 16px;
}

.service-status-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 8px;
}

.service-status-item {
  min-height: 52px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 12px;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
}

.service-name {
  margin-bottom: 2px;
  font-weight: 600;
}
</style>
