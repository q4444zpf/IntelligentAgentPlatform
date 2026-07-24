<template>
  <div class="mcp-page">
    <a-alert v-if="loadError" type="error" show-icon closable message="MCP 服务暂时不可用" :description="loadError" />

    <section class="mcp-heading">
      <div>
        <h2>MCP 客户端</h2>
        <p>统一管理智能体可调用的远程与本地工具服务</p>
      </div>
      <a-space wrap>
        <a-input v-model:value="query" allow-clear placeholder="搜索名称、标识或地址" class="search-input">
          <template #prefix><SearchOutlined /></template>
        </a-input>
        <a-select v-model:value="statusFilter" class="status-filter" :options="statusOptions" />
        <a-tooltip title="刷新客户端列表"><a-button aria-label="刷新客户端列表" :loading="loading" @click="loadClients"><template #icon><ReloadOutlined /></template></a-button></a-tooltip>
        <a-button type="primary" @click="openCreate"><template #icon><PlusOutlined /></template>添加 MCP</a-button>
      </a-space>
    </section>

    <div class="summary-strip">
      <div><span>客户端</span><strong>{{ clients.length }}</strong></div>
      <div><span>已启用</span><strong>{{ enabledCount }}</strong></div>
      <div><span>已发现工具</span><strong>{{ totalTools }}</strong></div>
      <div :class="{ warning: attentionCount }"><span>需要处理</span><strong>{{ attentionCount }}</strong></div>
    </div>

    <a-spin :spinning="loading && !clients.length">
      <div v-if="filteredClients.length" class="client-list">
        <article v-for="client in filteredClients" :key="client.key" class="client-row">
          <div class="transport-mark" :class="client.transport"><ApiOutlined /></div>
          <div class="client-main">
            <div class="client-title">
              <strong>{{ client.name }}</strong>
              <a-tag :color="transportColor(client.transport)">{{ transportLabel(client.transport) }}</a-tag>
              <span class="client-key">{{ client.key }}</span>
            </div>
            <p>{{ client.description || '暂无说明' }}</p>
            <div class="endpoint mono" :title="endpoint(client)">{{ endpoint(client) }}</div>
          </div>
          <div class="tool-state">
            <strong>{{ client.enabled_tool_count }} / {{ client.tool_count }}</strong>
            <span>工具已启用</span>
          </div>
          <div class="sync-state">
            <span :class="['state-dot', { online: client.enabled }]" />
            <strong>{{ client.enabled ? '已启用' : '已停用' }}</strong>
            <span>{{ syncText(client.last_synced_at) }}</span>
          </div>
          <div class="row-actions">
            <a-tooltip title="查看与同步工具"><a-button aria-label="查看与同步工具" @click="openTools(client)"><template #icon><AppstoreOutlined /></template></a-button></a-tooltip>
            <a-tooltip title="编辑配置"><a-button aria-label="编辑配置" @click="openEdit(client)"><template #icon><SettingOutlined /></template></a-button></a-tooltip>
            <a-switch :checked="client.enabled" :loading="busyKey === client.key" checked-children="开" un-checked-children="关" @change="toggleClient(client)" />
            <a-popconfirm title="删除后无法恢复，确定继续？" ok-text="删除" cancel-text="取消" @confirm="deleteClient(client)">
              <a-button danger aria-label="删除 MCP"><template #icon><DeleteOutlined /></template></a-button>
            </a-popconfirm>
          </div>
        </article>
      </div>
      <a-empty v-else :description="query || statusFilter !== 'all' ? '没有匹配的 MCP 客户端' : '还没有 MCP 客户端'">
        <a-button v-if="!query && statusFilter === 'all'" type="primary" @click="openCreate">添加第一个 MCP</a-button>
      </a-empty>
    </a-spin>

    <a-modal v-model:open="editorOpen" :title="editingKey ? '编辑 MCP 客户端' : '添加 MCP 客户端'" width="760px" :confirm-loading="saving" ok-text="保存" cancel-text="取消" @ok="saveClient">
      <a-tabs v-if="!editingKey" v-model:activeKey="editorMode" class="editor-tabs">
        <a-tab-pane key="form" tab="表单配置" />
        <a-tab-pane key="json" tab="JSON 导入" />
      </a-tabs>
      <div v-if="editorMode === 'json' && !editingKey" class="json-editor">
        <a-alert type="info" show-icon message="支持标准 mcpServers 配置，首次导入一个客户端。" />
        <a-textarea v-model:value="jsonConfig" :rows="15" spellcheck="false" />
      </div>
      <a-form v-else layout="vertical" class="client-form">
        <div class="form-grid two">
          <a-form-item label="客户端标识" required :validate-status="formErrors.key ? 'error' : undefined" :help="formErrors.key || '小写字母开头，可使用数字、- 和 _。'">
            <a-input v-model:value="form.key" :disabled="Boolean(editingKey)" placeholder="water-data" />
          </a-form-item>
          <a-form-item label="显示名称" required :validate-status="formErrors.name ? 'error' : undefined" :help="formErrors.name">
            <a-input v-model:value="form.name" placeholder="水情数据 MCP" />
          </a-form-item>
        </div>
        <a-form-item label="传输方式" required><a-segmented v-model:value="form.transport" block :options="transportOptions" /></a-form-item>
        <a-form-item v-if="form.transport !== 'stdio'" label="服务地址" required :validate-status="formErrors.endpoint ? 'error' : undefined" :help="formErrors.endpoint">
          <a-input v-model:value="form.url" placeholder="https://mcp.example.com/mcp" />
        </a-form-item>
        <template v-else>
          <div class="form-grid two">
            <a-form-item label="启动命令" required :validate-status="formErrors.endpoint ? 'error' : undefined" :help="formErrors.endpoint"><a-input v-model:value="form.command" placeholder="npx" /></a-form-item>
            <a-form-item label="工作目录"><a-input v-model:value="form.cwd" placeholder="可选" /></a-form-item>
          </div>
          <a-form-item label="命令参数" extra="每行一个参数，避免包含密钥。"><a-textarea v-model:value="argsText" :rows="3" placeholder="-y&#10;@example/mcp-server" /></a-form-item>
        </template>
        <a-form-item label="说明"><a-textarea v-model:value="form.description" :rows="2" maxlength="500" show-count /></a-form-item>
        <div class="form-grid two">
          <a-form-item v-if="form.transport !== 'stdio'" label="请求 Header" extra="每行 KEY=VALUE；令牌返回时会脱敏。"><a-textarea v-model:value="headersText" :rows="4" placeholder="Authorization=Bearer ..." /></a-form-item>
          <a-form-item v-else label="环境变量" extra="每行 KEY=VALUE；敏感值不会显示在列表。"><a-textarea v-model:value="envText" :rows="4" placeholder="API_KEY=..." /></a-form-item>
          <a-form-item label="初始状态"><a-switch v-model:checked="form.enabled" checked-children="启用" un-checked-children="停用" /></a-form-item>
        </div>
      </a-form>
    </a-modal>

    <a-drawer v-model:open="toolsOpen" width="min(620px, 100vw)" :title="`${activeClient?.name || ''} · 工具`">
      <div class="tools-toolbar">
        <div><strong>{{ selectedTools.length }} / {{ tools.length }}</strong><span> 个工具已允许</span></div>
        <a-space>
          <a-button @click="selectAllTools">全部允许</a-button>
          <a-button :loading="syncing" @click="syncTools"><template #icon><SyncOutlined /></template>同步工具</a-button>
          <a-button type="primary" :loading="savingTools" @click="saveTools">保存权限</a-button>
        </a-space>
      </div>
      <a-alert v-if="activeClient?.transport === 'stdio'" type="warning" show-icon message="stdio 客户端需由沙箱 Worker 执行工具发现。" />
      <a-spin :spinning="loadingTools">
        <a-checkbox-group v-model:value="selectedTools" class="tool-list">
          <label v-for="tool in tools" :key="tool.name" class="tool-item">
            <a-checkbox :value="tool.name" />
            <div><strong>{{ tool.name }}</strong><p>{{ tool.description || '暂无工具说明' }}</p><code>{{ schemaSummary(tool.input_schema) }}</code></div>
          </label>
        </a-checkbox-group>
        <a-empty v-if="!loadingTools && !tools.length" description="尚未发现工具，请先同步" />
      </a-spin>
    </a-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { message } from 'ant-design-vue';
import { ApiOutlined, AppstoreOutlined, DeleteOutlined, PlusOutlined, ReloadOutlined, SearchOutlined, SettingOutlined, SyncOutlined } from '@ant-design/icons-vue';
import { mcpApi, type McpClient, type McpClientInput, type McpTool, type McpTransport } from '@/api/mcp';

const emptyForm = (): McpClientInput & { key: string } => ({ key: '', name: '', description: '', transport: 'streamable_http', url: '', headers: {}, command: '', args: [], env: {}, cwd: '', enabled: true });
const clients = ref<McpClient[]>([]); const loading = ref(false); const loadError = ref(''); const query = ref(''); const statusFilter = ref('all'); const busyKey = ref('');
const editorOpen = ref(false); const editorMode = ref('form'); const editingKey = ref(''); const saving = ref(false); const form = reactive(emptyForm()); const formErrors = reactive({ key: '', name: '', endpoint: '' });
const argsText = ref(''); const headersText = ref(''); const envText = ref('');
const jsonConfig = ref(JSON.stringify({ mcpServers: { 'water-data': { name: '水情数据 MCP', transport: 'streamable_http', url: 'https://mcp.example.com/mcp', headers: { Authorization: 'Bearer <TOKEN>' } } } }, null, 2));
const toolsOpen = ref(false); const activeClient = ref<McpClient>(); const tools = ref<McpTool[]>([]); const selectedTools = ref<string[]>([]); const loadingTools = ref(false); const syncing = ref(false); const savingTools = ref(false);
const statusOptions = [{ label: '全部状态', value: 'all' }, { label: '已启用', value: 'enabled' }, { label: '已停用', value: 'disabled' }, { label: '未同步工具', value: 'unsynced' }];
const transportOptions = [{ label: 'Streamable HTTP', value: 'streamable_http' }, { label: 'SSE', value: 'sse' }, { label: 'stdio', value: 'stdio' }];
const enabledCount = computed(() => clients.value.filter((item) => item.enabled).length);
const totalTools = computed(() => clients.value.reduce((sum, item) => sum + item.tool_count, 0));
const attentionCount = computed(() => clients.value.filter((item) => item.enabled && !item.last_synced_at).length);
const filteredClients = computed(() => { const term = query.value.trim().toLowerCase(); return clients.value.filter((item) => { const matchesText = !term || [item.name, item.key, item.url, item.command].some((value) => value.toLowerCase().includes(term)); const matchesStatus = statusFilter.value === 'all' || (statusFilter.value === 'enabled' && item.enabled) || (statusFilter.value === 'disabled' && !item.enabled) || (statusFilter.value === 'unsynced' && !item.last_synced_at); return matchesText && matchesStatus; }); });

async function loadClients() { loading.value = true; loadError.value = ''; try { clients.value = await mcpApi.list(); } catch (error) { loadError.value = error instanceof Error ? error.message : '加载失败'; } finally { loading.value = false; } }
function resetForm() { Object.assign(form, emptyForm()); argsText.value = ''; headersText.value = ''; envText.value = ''; Object.assign(formErrors, { key: '', name: '', endpoint: '' }); }
function openCreate() { resetForm(); editingKey.value = ''; editorMode.value = 'form'; editorOpen.value = true; }
function openEdit(client: McpClient) { resetForm(); editingKey.value = client.key; Object.assign(form, client); argsText.value = client.args.join('\n'); headersText.value = stringifyPairs(client.headers); envText.value = stringifyPairs(client.env); editorMode.value = 'form'; editorOpen.value = true; }
function parsePairs(value: string) { return Object.fromEntries(value.split('\n').map((line) => line.trim()).filter(Boolean).map((line) => { const index = line.indexOf('='); return index > 0 ? [line.slice(0, index).trim(), line.slice(index + 1).trim()] : [line, '']; })); }
function stringifyPairs(value: Record<string, string>) { return Object.entries(value).map(([key, item]) => `${key}=${item}`).join('\n'); }
function normalizeImported() { const parsed = JSON.parse(jsonConfig.value) as Record<string, unknown>; const source = (parsed.mcpServers || parsed) as Record<string, Record<string, unknown>>; const entry = Object.entries(source)[0]; if (!entry) throw new Error('JSON 中没有 MCP 客户端'); const [key, value] = entry; const transport = (value.transport || value.type || (value.command ? 'stdio' : 'streamable_http')) as McpTransport; return { ...emptyForm(), ...value, key, name: String(value.name || key), transport, args: Array.isArray(value.args) ? value.args.map(String) : [], headers: (value.headers || {}) as Record<string, string>, env: (value.env || {}) as Record<string, string> }; }
function validate(payload: McpClientInput & { key: string }) { Object.assign(formErrors, { key: '', name: '', endpoint: '' }); if (!/^[a-z][a-z0-9_-]{0,63}$/.test(payload.key)) formErrors.key = '请输入有效的客户端标识'; if (!payload.name.trim()) formErrors.name = '请输入显示名称'; if (payload.transport === 'stdio' ? !payload.command.trim() : !/^https?:\/\//.test(payload.url)) formErrors.endpoint = payload.transport === 'stdio' ? '请输入启动命令' : '请输入有效的 HTTP(S) 地址'; return !Object.values(formErrors).some(Boolean); }
async function saveClient() { try { const payload = editorMode.value === 'json' && !editingKey.value ? normalizeImported() : { ...form, args: argsText.value.split('\n').map((item) => item.trim()).filter(Boolean), headers: parsePairs(headersText.value), env: parsePairs(envText.value) }; if (!validate(payload)) { editorMode.value = 'form'; return; } saving.value = true; if (editingKey.value) await mcpApi.update(editingKey.value, payload); else await mcpApi.create(payload); message.success(editingKey.value ? 'MCP 配置已更新' : 'MCP 客户端已创建'); editorOpen.value = false; await loadClients(); } catch (error) { message.error(error instanceof Error ? error.message : '保存失败'); } finally { saving.value = false; } }
async function toggleClient(client: McpClient) { busyKey.value = client.key; try { await mcpApi.toggle(client.key); await loadClients(); } catch (error) { message.error(error instanceof Error ? error.message : '切换状态失败'); } finally { busyKey.value = ''; } }
async function deleteClient(client: McpClient) { try { await mcpApi.remove(client.key); message.success('MCP 客户端已删除'); await loadClients(); } catch (error) { message.error(error instanceof Error ? error.message : '删除失败'); } }
async function openTools(client: McpClient) { activeClient.value = client; toolsOpen.value = true; loadingTools.value = true; try { tools.value = await mcpApi.tools(client.key); selectedTools.value = tools.value.filter((item) => item.enabled).map((item) => item.name); } catch (error) { message.error(error instanceof Error ? error.message : '加载工具失败'); } finally { loadingTools.value = false; } }
async function syncTools() { if (!activeClient.value) return; syncing.value = true; try { tools.value = await mcpApi.syncTools(activeClient.value.key); selectedTools.value = tools.value.filter((item) => item.enabled).map((item) => item.name); message.success(`已同步 ${tools.value.length} 个工具`); await loadClients(); } catch (error) { message.error(error instanceof Error ? error.message : '同步失败'); } finally { syncing.value = false; } }
async function saveTools() { if (!activeClient.value) return; savingTools.value = true; try { tools.value = await mcpApi.updateTools(activeClient.value.key, selectedTools.value.length === tools.value.length ? null : selectedTools.value); message.success('工具权限已保存'); await loadClients(); } catch (error) { message.error(error instanceof Error ? error.message : '保存失败'); } finally { savingTools.value = false; } }
function selectAllTools() { selectedTools.value = tools.value.map((item) => item.name); }
function endpoint(client: McpClient) { return client.transport === 'stdio' ? [client.command, ...client.args].join(' ') : client.url; }
function transportLabel(value: McpTransport) { return value === 'streamable_http' ? 'HTTP' : value.toUpperCase(); }
function transportColor(value: McpTransport) { return value === 'stdio' ? 'purple' : value === 'sse' ? 'cyan' : 'blue'; }
function syncText(value: string | null) { return value ? `同步于 ${new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}` : '工具尚未同步'; }
function schemaSummary(schema: Record<string, unknown>) { const properties = schema.properties as Record<string, unknown> | undefined; const names = properties ? Object.keys(properties) : []; return names.length ? `参数：${names.join('、')}` : '无参数'; }
onMounted(loadClients);
</script>

<style scoped>
.mcp-page { display: grid; gap: 16px; }
.mcp-heading { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 18px 20px; background: #fff; border: 1px solid #e1e9f2; border-radius: 8px; }
.mcp-heading h2 { margin: 0; color: #172033; font-size: 20px; }
.mcp-heading p { margin: 5px 0 0; color: #667085; }
.search-input { width: 260px; } .status-filter { width: 126px; }
.summary-strip { display: grid; grid-template-columns: repeat(4, 1fr); background: #fff; border: 1px solid #e1e9f2; border-radius: 8px; }
.summary-strip div { display: flex; align-items: baseline; justify-content: space-between; padding: 15px 20px; border-right: 1px solid #e8eef5; }
.summary-strip div:last-child { border-right: 0; } .summary-strip span { color: #667085; } .summary-strip strong { color: #173f67; font-size: 23px; } .summary-strip .warning strong { color: #b54708; }
.client-list { display: grid; gap: 10px; }
.client-row { display: grid; grid-template-columns: 44px minmax(260px, 1fr) 110px 145px auto; align-items: center; gap: 16px; min-height: 112px; padding: 16px 18px; background: #fff; border: 1px solid #dfe8f1; border-radius: 8px; transition: border-color .2s, box-shadow .2s; }
.client-row:hover { border-color: #a8c6e3; box-shadow: 0 5px 16px rgb(34 78 120 / 7%); }
.transport-mark { display: grid; width: 42px; height: 42px; place-items: center; color: #1768a8; background: #e9f3fb; border-radius: 6px; font-size: 20px; } .transport-mark.stdio { color: #6842a5; background: #f1ecfa; } .transport-mark.sse { color: #087e8b; background: #e5f7f7; }
.client-title { display: flex; align-items: center; gap: 8px; } .client-title strong { color: #172033; font-size: 15px; } .client-key { color: #7b8798; font: 12px Consolas, monospace; }
.client-main p { margin: 7px 0 5px; color: #596579; font-size: 13px; } .endpoint { overflow: hidden; color: #7b8798; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; } .mono { font-family: Consolas, "SFMono-Regular", monospace; }
.tool-state, .sync-state { display: flex; flex-direction: column; gap: 4px; } .tool-state strong { color: #173f67; font-size: 18px; } .tool-state span, .sync-state span { color: #7b8798; font-size: 12px; } .sync-state strong { font-size: 13px; }
.state-dot { display: inline-block; width: 7px; height: 7px; background: #98a2b3; border-radius: 50%; } .state-dot.online { background: #12a36d; box-shadow: 0 0 0 3px #dff5eb; }
.row-actions { display: flex; align-items: center; gap: 8px; }
.editor-tabs { margin-top: -8px; } .json-editor { display: grid; gap: 12px; } .json-editor textarea { font-family: Consolas, monospace; }
.form-grid { display: grid; gap: 14px; } .form-grid.two { grid-template-columns: 1fr 1fr; } .client-form :deep(.ant-form-item) { margin-bottom: 15px; }
.tools-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; padding-bottom: 14px; border-bottom: 1px solid #e7edf5; } .tools-toolbar span { color: #667085; }
.tool-list { display: grid; width: 100%; gap: 9px; margin-top: 14px; } .tool-item { display: grid; grid-template-columns: 22px 1fr; gap: 10px; padding: 13px; border: 1px solid #e1e9f2; border-radius: 6px; cursor: pointer; } .tool-item:hover { border-color: #9bbddd; } .tool-item p { margin: 5px 0; color: #667085; } .tool-item code { color: #356d9f; font-size: 12px; }
@media (max-width: 1100px) { .mcp-heading { align-items: flex-start; flex-direction: column; } .client-row { grid-template-columns: 44px 1fr auto; } .tool-state, .sync-state { display: none; } }
@media (max-width: 700px) { .mcp-heading :deep(.ant-space) { width: 100%; } .search-input { width: 100%; } .summary-strip { grid-template-columns: 1fr 1fr; } .summary-strip div:nth-child(2) { border-right: 0; } .summary-strip div:nth-child(-n+2) { border-bottom: 1px solid #e8eef5; } .client-row { grid-template-columns: 40px 1fr; } .row-actions { grid-column: 1 / -1; justify-content: flex-end; } .form-grid.two { grid-template-columns: 1fr; } .tools-toolbar { align-items: flex-start; flex-direction: column; } }
</style>
