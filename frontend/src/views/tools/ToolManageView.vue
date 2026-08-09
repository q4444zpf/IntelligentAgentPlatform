<template>
  <div class="tool-page">
    <a-alert
      v-if="loadError"
      type="error"
      show-icon
      closable
      message="工具注册中心暂时不可用"
      :description="loadError"
      @close="loadError = ''"
    />

    <header class="tool-heading">
      <div>
        <div class="heading-label">TOOL REGISTRY</div>
        <h2>工具注册中心</h2>
        <p>查看平台已注册工具的来源、风险和输入输出契约，并控制是否允许智能体调用。</p>
      </div>
      <a-space wrap>
        <a-input-search v-model:value="query" class="search-input" placeholder="搜索工具名称或说明" allow-clear />
        <a-button :loading="loading" aria-label="刷新工具列表" @click="loadTools">
          <template #icon><ReloadOutlined /></template>
        </a-button>
      </a-space>
    </header>

    <section class="summary-strip" aria-label="工具概览">
      <div><span>已注册</span><strong>{{ tools.length }}</strong></div>
      <div><span>已启用</span><strong>{{ enabledCount }}</strong></div>
      <div><span>系统内置</span><strong>{{ builtinCount }}</strong></div>
      <div><span>需审批</span><strong>{{ approvalCount }}</strong></div>
    </section>

    <section class="filter-bar">
      <a-select v-model:value="sourceFilter" class="filter-select" :options="sourceOptions" />
      <a-select v-model:value="riskFilter" class="filter-select" :options="riskOptions" />
      <a-select v-model:value="statusFilter" class="filter-select" :options="statusOptions" />
      <span>共 {{ filteredTools.length }} 项</span>
    </section>

    <a-spin :spinning="loading">
      <div v-if="filteredTools.length" class="tool-list">
        <article v-for="tool in filteredTools" :key="tool.tool_id" class="tool-item">
          <div class="tool-glyph"><ApiOutlined /></div>
          <div class="tool-main">
            <div class="tool-title">
              <strong>{{ tool.name }}</strong>
              <a-tag v-if="tool.is_builtin" color="blue">系统内置</a-tag>
              <a-tag v-if="tool.source === 'mcp'" color="cyan">MCP</a-tag>
              <a-tag :color="riskColor[tool.risk_level]">{{ riskLabel[tool.risk_level] }}</a-tag>
              <a-tag v-if="tool.requires_approval" color="gold">需审批</a-tag>
              <a-tag v-if="tool.source === 'mcp' && !tool.source_available" color="red">来源不可用</a-tag>
            </div>
            <p>{{ tool.description }}</p>
            <div class="tool-meta">
              <code>{{ tool.tool_id }}</code>
              <span>v{{ tool.version }}</span>
              <span>{{ sourceLabel[tool.source] }}</span>
              <span v-if="tool.source === 'mcp'">{{ tool.source_resource_id }} / {{ tool.source_capability_id }}</span>
            </div>
          </div>
          <div class="schema-summary">
            <span>输入 Schema</span>
            <strong>{{ schemaSummary(tool.input_schema) }}</strong>
            <span>输出 Schema</span>
            <strong>{{ schemaSummary(tool.output_schema) }}</strong>
          </div>
          <div class="tool-state">
            <a-switch
              :checked="tool.enabled"
              :loading="isToolPending(tool.tool_id)"
              :disabled="isToolPending(tool.tool_id)"
              checked-children="启用"
              un-checked-children="停用"
              @change="toggleTool(tool)"
            />
            <a-button
              v-if="!tool.published"
              size="small"
              type="link"
              aria-label="发布工具"
              :loading="isPublicationPending(tool.tool_id)"
              :disabled="!tool.source_available || isPublicationPending(tool.tool_id)"
              @click="setPublication(tool, true)"
            >发布工具</a-button>
            <a-button
              v-else
              size="small"
              type="link"
              aria-label="取消发布工具"
              :loading="isPublicationPending(tool.tool_id)"
              :disabled="isPublicationPending(tool.tool_id)"
              @click="setPublication(tool, false)"
            >取消发布</a-button>
            <span>{{ tool.published ? '已发布' : '未发布' }}</span>
          </div>
        </article>
      </div>
      <a-empty v-else :description="hasFilters ? '没有匹配的注册工具' : '暂无注册工具'" />
    </a-spin>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { message } from 'ant-design-vue';
import { ApiOutlined, ReloadOutlined } from '@ant-design/icons-vue';
import { toolsApi, type ToolInfo, type ToolRisk, type ToolSource } from '@/api/tools';

const tools = ref<ToolInfo[]>([]);
const loading = ref(false);
const loadError = ref('');
const query = ref('');
const sourceFilter = ref<'all' | ToolSource>('all');
const riskFilter = ref<'all' | ToolRisk>('all');
const statusFilter = ref<'all' | 'enabled' | 'disabled'>('all');
const pendingToolIds = ref(new Set<string>());
const pendingPublicationIds = ref(new Set<string>());
let loadRequestId = 0;
let loadController: AbortController | undefined;

const sourceLabel: Record<ToolSource, string> = {
  builtin: '系统内置',
  mcp: 'MCP',
  knowledge: '知识库',
  artifact: '成果组件',
  sandbox: '沙箱',
};
const riskLabel: Record<ToolRisk, string> = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
  critical: '严重风险',
};
const riskColor: Record<ToolRisk, string> = {
  low: 'green',
  medium: 'gold',
  high: 'orange',
  critical: 'red',
};
const sourceOptions = [
  { label: '全部来源', value: 'all' },
  ...Object.entries(sourceLabel).map(([value, label]) => ({ label, value })),
];
const riskOptions = [
  { label: '全部风险', value: 'all' },
  ...Object.entries(riskLabel).map(([value, label]) => ({ label, value })),
];
const statusOptions = [
  { label: '全部状态', value: 'all' },
  { label: '已启用', value: 'enabled' },
  { label: '已停用', value: 'disabled' },
];

const enabledCount = computed(() => tools.value.filter((tool) => tool.enabled).length);
const builtinCount = computed(() => tools.value.filter((tool) => tool.is_builtin).length);
const approvalCount = computed(() => tools.value.filter((tool) => tool.requires_approval).length);
const hasFilters = computed(() => Boolean(query.value.trim()) || sourceFilter.value !== 'all' || riskFilter.value !== 'all' || statusFilter.value !== 'all');
const filteredTools = computed(() => {
  const term = query.value.trim().toLowerCase();
  return tools.value.filter((tool) => {
    const matchesText = !term || `${tool.name} ${tool.tool_id} ${tool.description}`.toLowerCase().includes(term);
    const matchesSource = sourceFilter.value === 'all' || tool.source === sourceFilter.value;
    const matchesRisk = riskFilter.value === 'all' || tool.risk_level === riskFilter.value;
    const matchesStatus = statusFilter.value === 'all' || (statusFilter.value === 'enabled' ? tool.enabled : !tool.enabled);
    return matchesText && matchesSource && matchesRisk && matchesStatus;
  });
});

function schemaSummary(schema: Record<string, unknown>) {
  const properties = schema.properties;
  if (!properties || typeof properties !== 'object') return '无字段';
  const keys = Object.keys(properties);
  return keys.length ? keys.slice(0, 3).join('、') + (keys.length > 3 ? ` 等 ${keys.length} 项` : '') : '无字段';
}

function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === 'AbortError';
}

async function loadTools() {
  const requestId = ++loadRequestId;
  loadController?.abort();
  const controller = new AbortController();
  loadController = controller;
  loading.value = true;
  loadError.value = '';
  try {
    const result = await toolsApi.list(controller.signal);
    if (requestId !== loadRequestId) return;
    tools.value = result;
  } catch (error) {
    if (requestId !== loadRequestId || isAbortError(error)) return;
    loadError.value = error instanceof Error ? error.message : '加载失败';
  } finally {
    if (requestId === loadRequestId) {
      loading.value = false;
      if (loadController === controller) loadController = undefined;
    }
  }
}

const toggleRequests = new Map<string, symbol>();

function isToolPending(toolId: string) {
  return pendingToolIds.value.has(toolId);
}

function isPublicationPending(toolId: string) {
  return pendingPublicationIds.value.has(toolId);
}

const publicationRequests = new Map<string, symbol>();

async function setPublication(tool: ToolInfo, published: boolean) {
  if (isPublicationPending(tool.tool_id) || (published && !tool.source_available)) return;
  const requestToken = Symbol(tool.tool_id);
  publicationRequests.set(tool.tool_id, requestToken);
  pendingPublicationIds.value.add(tool.tool_id);
  try {
    const updated = await toolsApi.setPublished(tool.tool_id, published);
    if (publicationRequests.get(tool.tool_id) !== requestToken) return;
    tools.value = tools.value.map((item) => item.tool_id === updated.tool_id ? updated : item);
    message.success(updated.published ? '工具已发布' : '工具已取消发布');
  } catch (error) {
    if (publicationRequests.get(tool.tool_id) !== requestToken) return;
    message.error(error instanceof Error ? error.message : '更新发布状态失败');
  } finally {
    if (publicationRequests.get(tool.tool_id) === requestToken) {
      publicationRequests.delete(tool.tool_id);
      pendingPublicationIds.value.delete(tool.tool_id);
    }
  }
}

async function toggleTool(tool: ToolInfo) {
  if (isToolPending(tool.tool_id)) return;
  const requestToken = Symbol(tool.tool_id);
  toggleRequests.set(tool.tool_id, requestToken);
  pendingToolIds.value.add(tool.tool_id);
  try {
    const updated = await toolsApi.toggle(tool.tool_id);
    if (toggleRequests.get(tool.tool_id) !== requestToken) return;
    tools.value = tools.value.map((item) => item.tool_id === updated.tool_id ? updated : item);
    message.success(updated.enabled ? '工具已启用' : '工具已停用');
  } catch (error) {
    if (toggleRequests.get(tool.tool_id) !== requestToken) return;
    message.error(error instanceof Error ? error.message : '切换状态失败');
  } finally {
    if (toggleRequests.get(tool.tool_id) === requestToken) {
      toggleRequests.delete(tool.tool_id);
      pendingToolIds.value.delete(tool.tool_id);
    }
  }
}

onMounted(loadTools);
onBeforeUnmount(() => {
  loadRequestId += 1;
  loadController?.abort();
  toggleRequests.clear();
  publicationRequests.clear();
  pendingToolIds.value.clear();
  pendingPublicationIds.value.clear();
});
</script>

<style scoped>
.tool-page { display: grid; gap: 16px; }
.tool-heading { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 18px 20px; color: #172033; background: #fff; border: 1px solid #e1e9f2; border-left: 4px solid #17856b; border-radius: 8px; }
.heading-label { margin-bottom: 4px; color: #17856b; font: 700 10px Consolas, monospace; letter-spacing: 0; }
.tool-heading h2 { margin: 0; font-size: 20px; }
.tool-heading p { margin: 5px 0 0; color: #667085; }
.search-input { width: 260px; }
.summary-strip { display: grid; grid-template-columns: repeat(4, 1fr); background: #fff; border: 1px solid #e1e9f2; border-radius: 8px; }
.summary-strip div { display: flex; align-items: baseline; justify-content: space-between; padding: 14px 20px; border-right: 1px solid #e8eef5; }
.summary-strip div:last-child { border-right: 0; }
.summary-strip span, .filter-bar span { color: #667085; }
.summary-strip strong { color: #156b59; font-size: 23px; }
.filter-bar { display: flex; align-items: center; gap: 10px; }
.filter-bar > span { margin-left: auto; }
.filter-select { width: 130px; }
.tool-list { display: grid; gap: 10px; }
.tool-item { display: grid; grid-template-columns: 44px minmax(300px, 1fr) minmax(210px, 280px) 92px; align-items: center; gap: 16px; min-height: 116px; padding: 16px 18px; background: #fff; border: 1px solid #dfe8f1; border-radius: 8px; transition: border-color .2s, box-shadow .2s; }
.tool-item:hover { border-color: #9bc6bb; box-shadow: 0 5px 16px rgb(24 100 83 / 7%); }
.tool-glyph { display: grid; width: 42px; height: 42px; place-items: center; color: #156b59; background: #e7f5f0; border-radius: 6px; font-size: 20px; }
.tool-title { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; }
.tool-title strong { font: 700 15px Consolas, "Microsoft YaHei", monospace; }
.tool-main p { margin: 7px 0; color: #596579; font-size: 13px; }
.tool-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; color: #7b8798; font-size: 12px; }
.tool-meta code { color: #156b59; }
.schema-summary { display: grid; grid-template-columns: auto 1fr; gap: 4px 10px; min-width: 0; }
.schema-summary span { color: #7b8798; font-size: 12px; }
.schema-summary strong { overflow: hidden; color: #344054; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.tool-state { display: grid; justify-items: end; gap: 8px; }
.tool-state span { color: #98a2b3; font-size: 12px; }
@media (max-width: 1050px) { .tool-item { grid-template-columns: 44px 1fr auto; } .schema-summary { display: none; } }
@media (max-width: 700px) { .tool-heading { align-items: flex-start; flex-direction: column; } .tool-heading :deep(.ant-space), .search-input { width: 100%; } .summary-strip { grid-template-columns: 1fr 1fr; } .summary-strip div:nth-child(2) { border-right: 0; } .summary-strip div:nth-child(-n+2) { border-bottom: 1px solid #e8eef5; } .filter-bar { align-items: stretch; flex-direction: column; } .filter-select { width: 100%; } .filter-bar > span { margin-left: 0; } .tool-item { grid-template-columns: 40px 1fr; } .tool-state { grid-column: 1 / -1; grid-auto-flow: column; justify-content: end; align-items: center; } }
</style>
