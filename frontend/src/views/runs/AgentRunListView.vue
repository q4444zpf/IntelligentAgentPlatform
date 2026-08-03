<template>
  <div class="run-page">
    <a-alert v-if="listError" type="error" message="运行列表加载失败" :description="listError"><template #action><a-button aria-label="重试运行列表" @click="loadRuns">重试</a-button></template></a-alert>
    <header class="run-heading"><div><div class="heading-label">RUN AUDIT</div><h2>Agent Runs</h2><p>审阅智能体执行状态、事件轨迹与工具调用记录。</p></div><a-button :loading="loading" aria-label="刷新运行列表" @click="refresh"><template #icon><ReloadOutlined /></template></a-button></header>
    <section class="summary-strip" aria-label="运行概览"><div><span>总数</span><strong>{{ summary.total }}</strong></div><div><span>成功</span><strong>{{ summary.completed }}</strong></div><div><span>运行中</span><strong>{{ summary.running }}</strong></div><div><span>失败</span><strong>{{ summary.failed }}</strong></div><div><span>工具调用</span><strong>{{ summary.tool_invocations }}</strong></div></section>
    <section class="filter-bar"><a-select v-model:value="status" :options="statusOptions" @change="applyFilters" /><a-input v-model:value="actorId" placeholder="智能体 ID" @press-enter="applyFilters" /><a-range-picker @change="changeDates" /><a-input-search v-model:value="query" placeholder="会话标题或 Run ID" @search="applyFilters" /></section>
    <a-spin :spinning="loading">
      <div v-if="runs.length" class="table-shell"><table><thead><tr><th>状态</th><th>智能体</th><th>会话</th><th>触发摘要</th><th>工具数</th><th>开始时间</th><th>耗时</th><th>操作</th></tr></thead><tbody><tr v-for="item in runs" :key="item.id"><td><a-tag :color="statusColor(item.status)">{{ statusLabel(item.status) }}</a-tag></td><td><strong>{{ item.actor_id }}</strong><small>{{ item.actor_type }}</small></td><td><span>{{ item.conversation_title }}</span><code>{{ item.id }}</code></td><td>{{ item.trigger_summary || '-' }}</td><td>{{ item.tool_invocation_count }}</td><td>{{ formatTime(item.created_at) }}</td><td>{{ formatDuration(item.duration_ms) }}</td><td><a-button type="text" :aria-label="`查看运行 ${item.id}`" @click="openRun(item.id)"><template #icon><EyeOutlined /></template></a-button></td></tr></tbody></table></div>
      <a-empty v-else-if="!loading && !listError" description="暂无符合条件的运行记录" />
    </a-spin>
    <footer><span>共 {{ total }} 条</span><a-pagination :current="page" :page-size="pageSize" :total="total" show-size-changer @change="changePage" /></footer>
    <a-drawer v-model:open="drawerOpen" width="min(720px, 100vw)" title="运行详情" @close="closeDrawer">
      <div class="details">
        <section><h3>基础信息</h3><a-spin :spinning="detail.run.loading"><a-alert v-if="detail.run.error" type="error" message="基础信息读取失败" :description="detail.run.error"><template #action><a-button aria-label="重试运行详情" @click="retryDetail('run')">重试</a-button></template></a-alert><dl v-else-if="detail.run.data"><div><dt>Run ID</dt><dd>{{ detail.run.data.id }}</dd></div><div><dt>状态</dt><dd>{{ statusLabel(detail.run.data.status) }}</dd></div><div><dt>智能体</dt><dd>{{ detail.run.data.actor_id }}</dd></div><div><dt>会话 ID</dt><dd>{{ detail.run.data.conversation_id }}</dd></div></dl></a-spin></section>
        <section><h3>事件 Timeline</h3><a-spin :spinning="detail.events.loading"><a-alert v-if="detail.events.error" type="error" message="事件读取失败" :description="detail.events.error"><template #action><a-button aria-label="重试运行事件" @click="retryDetail('events')">重试</a-button></template></a-alert><ol v-else-if="detail.events.data?.length"><li v-for="event in detail.events.data" :key="event.sequence"><strong>#{{ event.sequence }}</strong> {{ event.event_type }}<pre>{{ JSON.stringify(event.payload, null, 2) }}</pre></li></ol><a-empty v-else-if="detail.events.data" description="暂无运行事件" /></a-spin></section>
        <section><h3>工具调用</h3><a-spin :spinning="detail.tools.loading"><a-alert v-if="detail.tools.error" type="error" message="工具调用读取失败" :description="detail.tools.error"><template #action><a-button aria-label="重试工具调用" @click="retryDetail('tools')">重试</a-button></template></a-alert><div v-else-if="detail.tools.data?.length" class="tools"><article v-for="tool in detail.tools.data" :key="tool.id"><div><strong>{{ tool.tool_id }}</strong> v{{ tool.tool_version }} <a-tag :color="statusColor(tool.status)">{{ statusLabel(tool.status) }}</a-tag></div><dl><div><dt>耗时</dt><dd>{{ formatDuration(tool.duration_ms) }}</dd></div><div><dt>错误码</dt><dd>{{ tool.error_code || '-' }}</dd></div></dl><span>参数摘要</span><pre>{{ JSON.stringify(tool.arguments_summary, null, 2) }}</pre><span>结果摘要</span><pre>{{ JSON.stringify(tool.result_summary, null, 2) }}</pre></article></div><a-empty v-else-if="detail.tools.data" description="本次运行未调用工具" /></a-spin></section>
      </div>
    </a-drawer>
  </div>
</template>
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { EyeOutlined, ReloadOutlined } from '@ant-design/icons-vue';
import { useRoute } from 'vue-router';
import { agentRunsApi, type AgentRunListItem, type RunEvent } from '@/api/agentRuns';
import { ApiError } from '@/api/client';
import type { AgentRunInfo } from '@/api/conversations';
import type { ToolInvocationInfo } from '@/api/tools';
const route = useRoute();
const initialRunId = typeof route.query.run_id === 'string' ? route.query.run_id : '';
let pendingDeepLink = initialRunId;
let deepLinkConsumed = false;
type Resource<T> = { data: T | null; loading: boolean; error: string };
type DetailKey = 'run' | 'events' | 'tools';
type DateBoundary = { startOf: (unit: 'day') => { toISOString: () => string }; endOf: (unit: 'day') => { toISOString: () => string } };
const runs = ref<AgentRunListItem[]>([]), total = ref(0), page = ref(1), pageSize = ref(20);
const summary = ref({ total: 0, completed: 0, running: 0, failed: 0, tool_invocations: 0 });
const status = ref('all'), actorId = ref(''), query = ref(''), startedAfter = ref(''), startedBefore = ref('');
const loading = ref(false), listError = ref(''), drawerOpen = ref(false), activeRunId = ref('');
const detail = ref({ run: { data: null, loading: false, error: '' } as Resource<AgentRunInfo>, events: { data: null, loading: false, error: '' } as Resource<RunEvent[]>, tools: { data: null, loading: false, error: '' } as Resource<ToolInvocationInfo[]> });
const cache = new Map<string, { run?: AgentRunInfo; events?: RunEvent[]; tools?: ToolInvocationInfo[] }>();
const statusOptions = [{label:'全部状态',value:'all'},{label:'排队中',value:'queued'},{label:'运行中',value:'running'},{label:'已完成',value:'completed'},{label:'失败',value:'failed'}];
let listController: AbortController | undefined, listId = 0, detailId = 0;
const detailRequests: Record<DetailKey, { controller?: AbortController; generation: number }> = {
  run: { generation: 0 }, events: { generation: 0 }, tools: { generation: 0 },
};
const errorText = (e: unknown) => e instanceof ApiError && e.status === 404 ? '记录不存在或无权访问' : e instanceof Error ? e.message : '加载失败';
const isAbort = (e: unknown) => e instanceof DOMException && e.name === 'AbortError';
function filters() { return { page: page.value, page_size: pageSize.value, ...(status.value !== 'all' ? {status:status.value}:{}), ...(actorId.value.trim()?{actor_id:actorId.value.trim()}:{}), ...(query.value.trim()?{query:query.value.trim()}:{}), ...(startedAfter.value?{started_after:startedAfter.value}:{}), ...(startedBefore.value?{started_before:startedBefore.value}:{}) }; }
async function loadRuns() { const id=++listId; listController?.abort(); const controller=new AbortController(); listController=controller; loading.value=true; listError.value=''; try { const result=await agentRunsApi.list(filters(),controller.signal); if(id!==listId)return; runs.value=result.items; total.value=result.total; summary.value=result.summary; if(pendingDeepLink&&!deepLinkConsumed){const runId=pendingDeepLink;deepLinkConsumed=true;pendingDeepLink='';openRun(runId);} } catch(e) { if(id!==listId||isAbort(e))return; listError.value=errorText(e); } finally { if(id===listId){loading.value=false;if(listController===controller)listController=undefined;} } }
function refresh(){const runId=drawerOpen.value?activeRunId.value:'';cache.clear();if(runId)openRun(runId);else cancelDetails();loadRuns();} function applyFilters(){page.value=1;loadRuns();}
function changeDates(dates:[DateBoundary,DateBoundary]|null){startedAfter.value=dates?.[0]?.startOf('day').toISOString()||'';startedBefore.value=dates?.[1]?.endOf('day').toISOString()||'';applyFilters();}
function changePage(next:number,size:number){const sizeChanged=size!==pageSize.value;pageSize.value=size;page.value=sizeChanged?1:next;loadRuns();}
function cancelDetails(){detailId++;(Object.keys(detailRequests) as DetailKey[]).forEach(key=>{const state=detailRequests[key];state.controller?.abort();state.controller=undefined;state.generation++;});}
function requestDetail<T>(key:DetailKey,id:number,runId:string,request:(s:AbortSignal)=>Promise<T>){
  if(detail.value[key].data)return;
  const state=detailRequests[key];state.controller?.abort();
  const controller=new AbortController();const generation=++state.generation;state.controller=controller;
  const isCurrent=()=>id===detailId&&activeRunId.value===runId&&state.generation===generation&&state.controller===controller;
  request(controller.signal).then(data=>{if(!isCurrent())return;(detail.value[key] as Resource<T>).data=data;const saved=cache.get(runId)||{};(saved as Record<string,unknown>)[key]=data;cache.set(runId,saved);}).catch(e=>{if(isCurrent()&&!isAbort(e))detail.value[key].error=errorText(e);}).finally(()=>{if(isCurrent()){detail.value[key].loading=false;state.controller=undefined;}});
}
function loadDetailResource(key:DetailKey,id:number,runId:string){if(key==='run')requestDetail('run',id,runId,s=>agentRunsApi.get(runId,s));else if(key==='events')requestDetail('events',id,runId,s=>agentRunsApi.listEvents(runId,s));else requestDetail('tools',id,runId,s=>agentRunsApi.listInvocations(runId,s));}
function retryDetail(key:DetailKey){const runId=activeRunId.value;if(!runId)return;detail.value[key].data=null;detail.value[key].error='';detail.value[key].loading=true;loadDetailResource(key,detailId,runId);}
function openRun(runId:string){cancelDetails();activeRunId.value=runId;drawerOpen.value=true;const saved=cache.get(runId)||{};detail.value={run:{data:saved.run||null,loading:!saved.run,error:''},events:{data:saved.events||null,loading:!saved.events,error:''},tools:{data:saved.tools||null,loading:!saved.tools,error:''}};const id=detailId;(['run','events','tools'] as DetailKey[]).forEach(key=>loadDetailResource(key,id,runId));}
function closeDrawer(){drawerOpen.value=false;activeRunId.value='';cancelDetails();}
function statusLabel(v:string){return ({queued:'排队中',running:'运行中',completed:'已完成',failed:'失败'} as Record<string,string>)[v]||v||'未知';}
function statusColor(v:string){return ({running:'blue',completed:'green',failed:'red'} as Record<string,string>)[v];}
function formatDuration(v:number|null|undefined){if(v==null||!Number.isFinite(v))return '-';if(v<1000)return `${Math.max(0,Math.round(v))} ms`;if(v<60000)return `${(v/1000).toFixed(2)} s`;return `${Math.floor(v/60000)}m ${((v%60000)/1000).toFixed(1)}s`;}
function formatTime(v:string){const d=new Date(v);return Number.isNaN(d.getTime())?'-':d.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'});}
onMounted(loadRuns);onBeforeUnmount(()=>{listId++;listController?.abort();cancelDetails();});
</script>
<style scoped>
.run-page{display:grid;gap:16px;min-width:0}.run-heading{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:16px 20px;background:#fff;border:1px solid #e1e9f2;border-left:4px solid #17856b;border-radius:8px}.heading-label{color:#17856b;font:700 10px Consolas,monospace;letter-spacing:0}.run-heading h2{margin:2px 0 0;font-size:20px}.run-heading p{margin:5px 0 0;color:#667085}.summary-strip{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));background:#fff;border:1px solid #e1e9f2;border-radius:8px}.summary-strip div{display:flex;justify-content:space-between;padding:13px 18px;border-right:1px solid #e8eef5}.summary-strip div:last-child{border:0}.summary-strip span{color:#667085}.summary-strip strong{color:#156b59;font-size:22px}.filter-bar{display:grid;grid-template-columns:150px minmax(150px,220px) 260px minmax(220px,1fr);gap:10px}.filter-bar>*{min-width:0;width:100%}.table-shell{overflow-x:auto;background:#fff;border:1px solid #dfe8f1;border-radius:8px}table{width:100%;min-width:1050px;border-collapse:collapse;table-layout:fixed}th,td{overflow:hidden;padding:12px;border-bottom:1px solid #e8eef5;text-align:left;text-overflow:ellipsis;white-space:nowrap}th{color:#667085;background:#f7f9fc;font-size:12px}td{color:#344054;font-size:13px}td strong,td small,td code,td span{display:block;overflow:hidden;text-overflow:ellipsis}td small,td code{color:#98a2b3;font-size:11px}footer{display:flex;align-items:center;justify-content:space-between;color:#667085}.details{display:grid;gap:22px}.details>section{min-width:0;padding-bottom:20px;border-bottom:1px solid #e8eef5}.details h3{font-size:15px}.details dl{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:10px 0}.details ol{padding-left:20px}.tools{display:grid;gap:18px}.tools article{padding-bottom:16px;border-bottom:1px solid #edf1f5}dt,.tools span{color:#7b8798;font-size:12px}dd{margin:3px 0;overflow-wrap:anywhere}pre{overflow:auto;max-width:100%;padding:10px;background:#f6f8fa;border:1px solid #e8eef5;border-radius:6px;font:12px/1.5 Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere}@media(max-width:1100px){.filter-bar{grid-template-columns:1fr 1fr}}@media(max-width:700px){.run-heading{align-items:flex-start}.summary-strip{grid-template-columns:1fr 1fr}.summary-strip div:nth-child(2n){border-right:0}.summary-strip div:last-child{grid-column:1/-1}.filter-bar{grid-template-columns:1fr}footer{align-items:flex-start;flex-direction:column}.details dl{grid-template-columns:1fr}}
</style>
