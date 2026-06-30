<template>
  <div class="page-grid">
    <div class="metric-grid">
      <a-card v-for="item in metrics" :key="item.label" class="metric-card">
        <div class="metric-label">{{ item.label }}</div>
        <div class="metric-value">{{ item.value }}</div>
        <a-tag :color="item.color">{{ item.hint }}</a-tag>
      </a-card>
    </div>

    <a-row :gutter="16">
      <a-col :span="14">
        <a-card class="section-card" title="平台核心闭环">
          <a-steps direction="vertical" :current="2" size="small" :items="steps" />
        </a-card>
      </a-col>
      <a-col :span="10">
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
      <a-col :span="12">
        <a-card class="section-card" title="资源运行概览">
          <a-table :columns="resourceColumns" :data-source="resourceRows" :pagination="false" row-key="name">
            <template #bodyCell="{ column, record }">
              <a-progress v-if="column.key === 'health'" :percent="record.health" size="small" />
              <a-tag v-if="column.key === 'status'" :color="record.color">{{ record.status }}</a-tag>
            </template>
          </a-table>
        </a-card>
      </a-col>
      <a-col :span="12">
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
const metrics = [
  { label: '智能体', value: 18, hint: '个人 / 公用 / 系统', color: 'blue' },
  { label: 'MCP / Skill', value: 33, hint: '沙箱受控调用', color: 'green' },
  { label: '流程实例', value: 126, hint: '今日运行', color: 'cyan' },
  { label: '待审核', value: 7, hint: '公用发布', color: 'gold' },
];

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
</script>
