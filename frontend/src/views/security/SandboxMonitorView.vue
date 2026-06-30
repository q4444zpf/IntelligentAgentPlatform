<template>
  <div class="page-grid">
    <a-alert
      type="info"
      show-icon
      message="Web 版智能体沙箱模式"
      description="用户自建 MCP、Skill、脚本和文件处理任务均通过沙箱或受限 Worker 执行，默认禁止访问宿主机敏感目录和内网敏感地址。"
    />

    <a-card class="section-card" title="沙箱执行任务">
      <a-table :columns="columns" :data-source="tasks" row-key="id">
        <template #bodyCell="{ column, record }">
          <a-tag v-if="column.key === 'type'" color="blue">{{ taskTypeText[record.type] }}</a-tag>
          <a-tag v-if="column.key === 'risk'" :color="riskColor[record.risk]">{{ riskText[record.risk] }}</a-tag>
          <a-tag v-if="column.key === 'status'" :color="statusColor[record.status]">{{ sandboxStatusText[record.status] }}</a-tag>
          <a-space v-if="column.key === 'action'">
            <a>日志</a>
            <a>策略</a>
          </a-space>
        </template>
      </a-table>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue';

import { getSandboxTasks } from '@/mock/api';
import type { SandboxTask } from '@/types/platform';
import { statusColor } from '@/utils/format';

const tasks = ref<SandboxTask[]>([]);
const taskTypeText: Record<string, string> = {
  mcp: 'MCP',
  skill: 'Skill',
  file: '文件处理',
  script: '脚本',
};
const riskText: Record<string, string> = {
  low: '低',
  medium: '中',
  high: '高',
};
const riskColor: Record<string, string> = {
  low: 'green',
  medium: 'orange',
  high: 'red',
};
const sandboxStatusText: Record<string, string> = {
  running: '执行中',
  success: '成功',
  blocked: '已阻断',
  failed: '失败',
};
const columns = [
  { title: '任务名称', dataIndex: 'taskName' },
  { title: '来源资源', dataIndex: 'source' },
  { title: '类型', key: 'type', width: 100 },
  { title: '隔离策略', dataIndex: 'isolation', width: 220 },
  { title: '风险', key: 'risk', width: 90 },
  { title: '状态', key: 'status', width: 110 },
  { title: '耗时', dataIndex: 'duration', width: 90 },
  { title: '操作', key: 'action', width: 110 },
];

onMounted(async () => {
  tasks.value = await getSandboxTasks();
});
</script>
