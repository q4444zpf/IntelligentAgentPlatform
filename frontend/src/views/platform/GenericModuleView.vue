<template>
  <div class="page-grid">
    <a-card class="section-card">
      <div class="toolbar">
        <div>
          <a-typography-title :level="4" style="margin: 0">{{ config.title }}</a-typography-title>
          <a-typography-text type="secondary">{{ config.description }}</a-typography-text>
        </div>
        <a-space>
          <a-button>{{ config.secondaryAction }}</a-button>
          <a-button type="primary">{{ config.primaryAction }}</a-button>
        </a-space>
      </div>

      <a-row :gutter="16">
        <a-col v-for="metric in config.metrics" :key="metric.label" :span="6">
          <a-card class="metric-card">
            <div class="metric-label">{{ metric.label }}</div>
            <div class="metric-value">{{ metric.value }}</div>
            <a-tag :color="metric.color">{{ metric.hint }}</a-tag>
          </a-card>
        </a-col>
      </a-row>
    </a-card>

    <a-card class="section-card" :title="config.tableTitle">
      <a-table :columns="columns" :data-source="config.rows" row-key="id" :pagination="{ pageSize: 5 }">
        <template #bodyCell="{ column, record }">
          <a-tag v-if="column.key === 'status'" :color="record.statusColor">{{ record.status }}</a-tag>
          <a-space v-if="column.key === 'action'">
            <a>查看</a>
            <a>配置</a>
            <a v-if="config.canPublish">发布</a>
          </a-space>
        </template>
      </a-table>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useRoute } from 'vue-router';

interface RowItem {
  id: string;
  name: string;
  type: string;
  owner: string;
  status: string;
  statusColor: string;
  updatedAt: string;
}

const route = useRoute();

const moduleConfigs: Record<string, {
  title: string;
  description: string;
  primaryAction: string;
  secondaryAction: string;
  tableTitle: string;
  canPublish?: boolean;
  metrics: Array<{ label: string; value: number; hint: string; color: string }>;
  rows: RowItem[];
}> = {
  agent: {
    title: '智能体管理',
    description: '创建、配置、调试和发布可被用户、流程、API 调用的智能体。',
    primaryAction: '新建智能体',
    secondaryAction: '调试测试',
    tableTitle: '智能体列表',
    canPublish: true,
    metrics: [
      { label: '智能体总数', value: 18, hint: '含个人和公用', color: 'blue' },
      { label: '已发布', value: 11, hint: '可被调用', color: 'green' },
      { label: '绑定 Skill', value: 36, hint: '工具能力', color: 'cyan' },
      { label: '待审核', value: 3, hint: '发布流程', color: 'gold' },
    ],
    rows: [
      { id: 'a1', name: '知识库问答智能体', type: '问答型', owner: '平台管理员', status: '已发布', statusColor: 'green', updatedAt: '2026-06-29 10:10' },
      { id: 'a2', name: '流程执行智能体', type: '流程型', owner: '平台演示用户', status: '私有可用', statusColor: 'blue', updatedAt: '2026-06-28 16:20' },
      { id: 'a3', name: '工具调用智能体', type: '工具型', owner: '平台演示用户', status: '待审核', statusColor: 'gold', updatedAt: '2026-06-28 14:42' },
    ],
  },
  prompt: {
    title: 'Prompt 管理',
    description: '维护系统、角色、工具调用、输出格式和安全约束 Prompt。',
    primaryAction: '新建 Prompt',
    secondaryAction: '版本对比',
    tableTitle: 'Prompt 模板',
    metrics: [
      { label: '模板数', value: 42, hint: '统一维护', color: 'blue' },
      { label: '已发布', value: 29, hint: '可绑定', color: 'green' },
      { label: '变量数', value: 86, hint: '上下文注入', color: 'cyan' },
      { label: '调试记录', value: 218, hint: '效果追踪', color: 'purple' },
    ],
    rows: [
      { id: 'p1', name: '通用问答系统 Prompt', type: '系统', owner: '平台管理员', status: '已发布', statusColor: 'green', updatedAt: '2026-06-27 13:20' },
      { id: 'p2', name: '工具调用参数补全 Prompt', type: '工具调用', owner: 'AI 服务组', status: '已发布', statusColor: 'green', updatedAt: '2026-06-26 18:11' },
      { id: 'p3', name: '安全输出约束 Prompt', type: '安全约束', owner: '安全管理员', status: '草稿', statusColor: 'default', updatedAt: '2026-06-25 09:18' },
    ],
  },
  mcp: {
    title: 'MCP 管理',
    description: '维护远程、平台托管和第三方 MCP Server，统一同步工具列表和调用审计。',
    primaryAction: '新建 MCP',
    secondaryAction: '同步工具',
    tableTitle: 'MCP Server',
    canPublish: true,
    metrics: [
      { label: 'MCP 数量', value: 9, hint: '个人与公用', color: 'blue' },
      { label: '可用工具', value: 51, hint: 'Schema 已同步', color: 'green' },
      { label: '沙箱运行', value: 7, hint: '受控执行', color: 'purple' },
      { label: '连接异常', value: 1, hint: '需处理', color: 'red' },
    ],
    rows: [
      { id: 'm1', name: '文件解析 MCP', type: '平台托管', owner: '平台管理员', status: '已发布', statusColor: 'green', updatedAt: '2026-06-29 09:10' },
      { id: 'm2', name: '数据库查询 MCP', type: '远程 HTTP', owner: '数据组', status: '私有可用', statusColor: 'blue', updatedAt: '2026-06-28 15:05' },
      { id: 'm3', name: '办公文档 MCP', type: '第三方', owner: '平台演示用户', status: '待审核', statusColor: 'gold', updatedAt: '2026-06-27 11:30' },
    ],
  },
  skill: {
    title: 'Skill / Tool 管理',
    description: '注册 HTTP API、文件处理、消息通知、人工确认等确定性工具。',
    primaryAction: '注册 Skill',
    secondaryAction: '调试调用',
    tableTitle: 'Skill 列表',
    canPublish: true,
    metrics: [
      { label: 'Skill 数', value: 24, hint: '确定性能力', color: 'blue' },
      { label: 'HTTP API', value: 16, hint: '第一版重点', color: 'green' },
      { label: '需确认', value: 5, hint: '高风险动作', color: 'orange' },
      { label: '调用失败', value: 2, hint: '近 24 小时', color: 'red' },
    ],
    rows: [
      { id: 's1', name: '发送通知 Skill', type: '消息通知', owner: '平台管理员', status: '已发布', statusColor: 'green', updatedAt: '2026-06-29 08:44' },
      { id: 's2', name: '工单创建 Skill', type: 'HTTP API', owner: '业务系统组', status: '私有可用', statusColor: 'blue', updatedAt: '2026-06-28 17:12' },
      { id: 's3', name: '文件导出 Skill', type: '文件处理', owner: '平台演示用户', status: '待审核', statusColor: 'gold', updatedAt: '2026-06-27 20:22' },
    ],
  },
  knowledge: {
    title: '知识库管理',
    description: '管理文档上传、解析、切片、向量化、检索测试和引用溯源。',
    primaryAction: '新建知识库',
    secondaryAction: '检索测试',
    tableTitle: '知识库列表',
    metrics: [
      { label: '知识库', value: 12, hint: '按权限隔离', color: 'blue' },
      { label: '文档数', value: 1386, hint: '已入库', color: 'green' },
      { label: '分段数', value: 28400, hint: '向量索引', color: 'cyan' },
      { label: '命中率', value: 91, hint: '测试集', color: 'purple' },
    ],
    rows: [
      { id: 'k1', name: '平台使用手册', type: '文档库', owner: '平台管理员', status: '启用', statusColor: 'green', updatedAt: '2026-06-28 10:00' },
      { id: 'k2', name: '业务制度知识库', type: '制度库', owner: '业务专家', status: '启用', statusColor: 'green', updatedAt: '2026-06-27 12:16' },
      { id: 'k3', name: '接口文档知识库', type: 'API 文档', owner: '集成组', status: '重建中', statusColor: 'processing', updatedAt: '2026-06-26 19:40' },
    ],
  },
  workflow: {
    title: '流程编排',
    description: '编排开始、智能体、Skill、MCP Tool、条件、人工确认、输出节点。',
    primaryAction: '新建流程',
    secondaryAction: '调试运行',
    tableTitle: '流程定义',
    metrics: [
      { label: '流程数', value: 15, hint: '基础编排', color: 'blue' },
      { label: '运行中', value: 4, hint: '实例', color: 'processing' },
      { label: '人工待办', value: 6, hint: '确认节点', color: 'gold' },
      { label: '失败节点', value: 1, hint: '需排查', color: 'red' },
    ],
    rows: [
      { id: 'w1', name: '知识问答处理流程', type: 'AI + RAG', owner: '平台管理员', status: '已发布', statusColor: 'green', updatedAt: '2026-06-29 09:00' },
      { id: 'w2', name: '工具调用审批流程', type: 'Tool + 人工确认', owner: '安全管理员', status: '已发布', statusColor: 'green', updatedAt: '2026-06-28 18:44' },
      { id: 'w3', name: '报告生成流程', type: 'Agent + Skill', owner: '平台演示用户', status: '草稿', statusColor: 'default', updatedAt: '2026-06-27 16:02' },
    ],
  },
  collaboration: {
    title: '多智能体协同',
    description: '配置主智能体、子智能体、执行顺序、输入输出映射和汇总策略。',
    primaryAction: '新建协同',
    secondaryAction: '运行测试',
    tableTitle: '协同配置',
    metrics: [
      { label: '协同任务', value: 8, hint: '编排式', color: 'blue' },
      { label: '子智能体', value: 23, hint: '参与执行', color: 'green' },
      { label: '今日运行', value: 46, hint: '可追溯', color: 'cyan' },
      { label: '冲突意见', value: 2, hint: '需人工复核', color: 'orange' },
    ],
    rows: [
      { id: 'c1', name: '资料研判协同', type: '主从模式', owner: '平台管理员', status: '已发布', statusColor: 'green', updatedAt: '2026-06-29 08:30' },
      { id: 'c2', name: '报告生成协同', type: '流水线', owner: '业务专家', status: '私有可用', statusColor: 'blue', updatedAt: '2026-06-28 14:21' },
      { id: 'c3', name: '多意见会商协同', type: '汇总协同', owner: '平台演示用户', status: '草稿', statusColor: 'default', updatedAt: '2026-06-27 15:20' },
    ],
  },
  integration: {
    title: '系统集成',
    description: '管理 OpenAPI、API Token、Webhook、嵌入式对话框和第三方系统调用。',
    primaryAction: '创建 API 应用',
    secondaryAction: '生成 Token',
    tableTitle: 'API 应用',
    metrics: [
      { label: 'API 应用', value: 7, hint: '第三方接入', color: 'blue' },
      { label: 'Token', value: 13, hint: '可停用', color: 'green' },
      { label: '今日调用', value: 1286, hint: 'OpenAPI', color: 'cyan' },
      { label: 'Webhook', value: 5, hint: '事件回调', color: 'purple' },
    ],
    rows: [
      { id: 'i1', name: '业务系统嵌入应用', type: 'JS SDK', owner: '集成组', status: '启用', statusColor: 'green', updatedAt: '2026-06-29 09:12' },
      { id: 'i2', name: '流程触发 OpenAPI', type: 'REST API', owner: '平台管理员', status: '启用', statusColor: 'green', updatedAt: '2026-06-28 10:18' },
      { id: 'i3', name: '消息回调 Webhook', type: 'Webhook', owner: '平台演示用户', status: '停用', statusColor: 'default', updatedAt: '2026-06-27 09:30' },
    ],
  },
  users: {
    title: '用户与权限',
    description: '维护用户、角色、权限码、菜单权限和资源访问范围。',
    primaryAction: '新建用户',
    secondaryAction: '角色授权',
    tableTitle: '用户列表',
    metrics: [
      { label: '用户数', value: 68, hint: '平台账号', color: 'blue' },
      { label: '角色数', value: 9, hint: 'RBAC', color: 'green' },
      { label: '第三方账号', value: 6, hint: 'API 应用', color: 'cyan' },
      { label: '停用账号', value: 2, hint: '安全治理', color: 'red' },
    ],
    rows: [
      { id: 'u1', name: '平台演示用户', type: '平台管理员', owner: '系统', status: '启用', statusColor: 'green', updatedAt: '2026-06-29 08:00' },
      { id: 'u2', name: '审核人员', type: '发布审核', owner: '系统', status: '启用', statusColor: 'green', updatedAt: '2026-06-28 18:00' },
      { id: 'u3', name: '第三方系统账号', type: 'API 账号', owner: '集成组', status: '启用', statusColor: 'green', updatedAt: '2026-06-27 16:40' },
    ],
  },
  audit: {
    title: '审计与日志',
    description: '查看登录、操作、模型调用、Tool 调用、流程运行和发布审核日志。',
    primaryAction: '导出日志',
    secondaryAction: '高级筛选',
    tableTitle: '审计日志',
    metrics: [
      { label: '今日日志', value: 3026, hint: '全链路', color: 'blue' },
      { label: 'Tool 调用', value: 682, hint: '可追溯', color: 'green' },
      { label: '模型调用', value: 1398, hint: 'Token 统计', color: 'cyan' },
      { label: '阻断事件', value: 3, hint: '沙箱策略', color: 'red' },
    ],
    rows: [
      { id: 'l1', name: '调用知识库问答智能体', type: '智能体调用', owner: '平台演示用户', status: '成功', statusColor: 'green', updatedAt: '2026-06-29 10:42' },
      { id: 'l2', name: '提交 MCP 公用发布', type: '发布审核', owner: '平台演示用户', status: '待审核', statusColor: 'gold', updatedAt: '2026-06-29 10:12' },
      { id: 'l3', name: '阻断内网地址访问', type: '沙箱策略', owner: 'Sandbox Executor', status: '已阻断', statusColor: 'red', updatedAt: '2026-06-29 09:58' },
    ],
  },
  settings: {
    title: '系统设置',
    description: '维护租户、密钥、配置中心、监控告警和平台基础参数。',
    primaryAction: '保存配置',
    secondaryAction: '配置检查',
    tableTitle: '系统配置项',
    metrics: [
      { label: '配置项', value: 34, hint: '运行参数', color: 'blue' },
      { label: '密钥引用', value: 12, hint: '加密存储', color: 'green' },
      { label: '告警规则', value: 8, hint: '监控治理', color: 'orange' },
      { label: '租户', value: 3, hint: '隔离空间', color: 'purple' },
    ],
    rows: [
      { id: 'set1', name: '默认沙箱超时', type: '安全配置', owner: '系统', status: '启用', statusColor: 'green', updatedAt: '2026-06-29 09:00' },
      { id: 'set2', name: 'OpenAPI 限流策略', type: '网关配置', owner: '系统', status: '启用', statusColor: 'green', updatedAt: '2026-06-28 15:20' },
      { id: 'set3', name: '审计日志保存周期', type: '审计配置', owner: '系统', status: '启用', statusColor: 'green', updatedAt: '2026-06-27 13:30' },
    ],
  },
};

const defaultConfig = moduleConfigs.agent;
const config = computed(() => moduleConfigs[String(route.meta.module)] || defaultConfig);

const columns = [
  { title: '名称', dataIndex: 'name' },
  { title: '类型', dataIndex: 'type', width: 160 },
  { title: '负责人', dataIndex: 'owner', width: 150 },
  { title: '状态', key: 'status', width: 120 },
  { title: '更新时间', dataIndex: 'updatedAt', width: 180 },
  { title: '操作', key: 'action', width: 150 },
];
</script>
