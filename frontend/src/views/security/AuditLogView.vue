<template>
  <div class="audit-page">
    <a-alert v-if="listError" type="error" message="审计列表加载失败" :description="listError"><template #action><a-button aria-label="重试审计列表" @click="loadEvents">重试</a-button></template></a-alert>
    <header class="audit-heading"><div><div class="heading-label">AUDIT TRACE</div><h2>统一审计中心</h2></div><a-button :loading="loading" aria-label="刷新审计列表" @click="loadEvents"><template #icon><ReloadOutlined /></template></a-button></header>
    <section class="summary-strip" aria-label="审计概览"><div><span>总事件</span><strong>{{ summary.total }}</strong></div><div><span>失败</span><strong>{{ summary.failed }}</strong></div><div><span>高风险</span><strong>{{ summary.high_risk }}</strong></div><div><span>运行事件</span><strong>{{ summary.runtime }}</strong></div><div><span>管理事件</span><strong>{{ summary.management }}</strong></div></section>
    <section class="filter-bar" aria-label="筛选审计事件">
      <a-select v-model:value="category" aria-label="审计类别" data-test="category-filter" class="category-filter" :options="categoryOptions" @change="applyFilters" />
      <a-select v-model:value="source" aria-label="审计来源" data-test="source-filter" :options="sourceOptions" @change="applyFilters" />
      <a-select v-model:value="status" aria-label="审计状态" data-test="status-filter" :options="statusOptions" @change="applyFilters" />
      <a-select v-model:value="risk" aria-label="风险等级" data-test="risk-filter" :options="riskOptions" @change="applyFilters" />
      <a-input v-model:value="action" aria-label="操作类型" placeholder="操作类型" @press-enter="applyFilters" />
      <a-input v-model:value="projectId" aria-label="项目 ID" placeholder="项目 ID" @press-enter="applyFilters" />
      <a-input v-model:value="userId" aria-label="用户 ID" placeholder="用户 ID" @press-enter="applyFilters" />
      <a-range-picker aria-label="发生日期" @change="changeDates" />
      <a-input-search v-model:value="query" aria-label="关键词" placeholder="事件、Trace 或资源 ID" @search="applyFilters" />
    </section>
    <a-spin :spinning="loading">
      <div v-if="events.length" class="table-shell"><table><thead><tr><th>时间</th><th>状态</th><th>风险</th><th>来源</th><th>操作</th><th>资源</th><th>Trace / Run</th><th>操作</th></tr></thead><tbody><tr v-for="item in events" :key="item.id"><td>{{ formatTime(item.occurred_at) }}</td><td><a-tag :color="statusColor(item.status)">{{ enumLabel(statusLabels,item.status) }}</a-tag></td><td><a-tag :color="riskColor(item.risk_level)">{{ enumLabel(riskLabels,item.risk_level) }}</a-tag></td><td>{{ enumLabel(sourceLabels,item.source) }}</td><td><strong>{{ item.action }}</strong><small>{{ item.id }}</small></td><td><span>{{ item.resource_name || item.resource_id || '-' }}</span><small>{{ item.resource_type || '-' }}</small></td><td><code>{{ item.trace_id || '-' }}</code><button v-if="item.source==='agent'&&item.run_id" class="run-link" :aria-label="`打开运行 ${item.run_id}`" @click="openRun(item.run_id)">{{ item.run_id }}</button><small v-else>{{ item.run_id || '-' }}</small></td><td><a-button type="text" :aria-label="`查看审计事件 ${item.id}`" @click="openEvent(item.id)"><template #icon><EyeOutlined /></template></a-button></td></tr></tbody></table></div>
      <a-empty v-else-if="!loading&&!listError" description="暂无符合条件的审计事件" />
    </a-spin>
    <footer><span>共 {{ total }} 条</span><a-pagination :current="page" :page-size="pageSize" :total="total" show-size-changer @change="changePage" /></footer>
    <a-drawer v-model:open="drawerOpen" width="min(720px, 100vw)" title="审计事件详情" @close="closeDrawer">
      <div class="details">
        <section><h3>事件详情</h3><a-spin :spinning="detail.loading"><a-alert v-if="detail.error" type="error" message="审计详情读取失败" :description="detail.error"><template #action><a-button aria-label="重试审计详情" @click="retryDetail">重试</a-button></template></a-alert><template v-else-if="detail.data"><dl><div><dt>事件 ID</dt><dd>{{ detail.data.id }}</dd></div><div><dt>用户</dt><dd>{{ detail.data.user_id || '-' }}</dd></div><div><dt>角色</dt><dd>{{ detail.data.actor_role || '-' }}</dd></div><div><dt>错误码</dt><dd>{{ detail.data.error_code || '-' }}</dd></div></dl><p class="summary">{{ detail.data.summary || '-' }}</p><pre>{{ safeJson(detail.data.metadata) }}</pre></template></a-spin></section>
        <section class="trace-section"><h3>Trace 时间线</h3><a-spin :spinning="related.loading"><a-alert v-if="related.error" type="error" message="关联事件读取失败" :description="related.error"><template #action><a-button aria-label="重试关联事件" @click="retryRelated">重试</a-button></template></a-alert><ol v-else-if="sortedRelated.length" class="trace"><li v-for="item in sortedRelated" :key="item.id" class="trace-item" :data-event-id="item.id"><time>{{ formatTime(item.occurred_at) }}</time><div><strong>{{ item.action }}</strong><span>{{ enumLabel(sourceLabels,item.source) }} · {{ enumLabel(statusLabels,item.status) }}</span><code>{{ item.id }}</code></div></li></ol><a-empty v-else-if="related.data" description="暂无关联事件" /></a-spin></section>
      </div>
    </a-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { EyeOutlined, ReloadOutlined } from '@ant-design/icons-vue';
import { useRouter } from 'vue-router';
import { auditApi, type AuditEventDetail, type AuditEventListItem, type AuditFilters } from '@/api/audit';
import { ApiError } from '@/api/client';
type Resource<T>={data:T|null;loading:boolean;error:string};
type DateBoundary={startOf:(unit:'day')=>{toISOString:()=>string};endOf:(unit:'day')=>{toISOString:()=>string}};
const router=useRouter();
const events=ref<AuditEventListItem[]>([]),total=ref(0),page=ref(1),pageSize=ref(20),loading=ref(false),listError=ref('');
const summary=ref({total:0,failed:0,high_risk:0,runtime:0,management:0,by_source:{}});
const category=ref('all'),source=ref('all'),status=ref('all'),risk=ref('all'),action=ref(''),projectId=ref(''),userId=ref(''),query=ref(''),occurredAfter=ref(''),occurredBefore=ref('');
const drawerOpen=ref(false),activeId=ref('');
const detail=ref<Resource<AuditEventDetail>>({data:null,loading:false,error:''}),related=ref<Resource<AuditEventListItem[]>>({data:null,loading:false,error:''});
const categoryOptions=[{label:'全部类别',value:'all'},{label:'运行',value:'runtime'},{label:'管理',value:'management'}];
const sourceOptions=['all','agent','tool','mcp','knowledge','sandbox','llm','system'].map(value=>({label:value==='all'?'全部来源':value,value}));
const statusOptions=['all','started','succeeded','failed','cancelled'].map(value=>({label:value==='all'?'全部状态':value,value}));
const riskOptions=['all','low','medium','high','critical'].map(value=>({label:value==='all'?'全部风险':value,value}));
const statusLabels:Record<string,string>={started:'进行中',succeeded:'成功',failed:'失败',cancelled:'已取消'};
const riskLabels:Record<string,string>={low:'低',medium:'中',high:'高',critical:'严重'};
const sourceLabels:Record<string,string>={agent:'Agent',tool:'工具',mcp:'MCP',knowledge:'知识库',sandbox:'沙箱',llm:'LLM',system:'系统'};
let listController:AbortController|undefined,listGeneration=0,drawerGeneration=0;
const detailRequest={controller:undefined as AbortController|undefined,generation:0};
const relatedRequest={controller:undefined as AbortController|undefined,generation:0};
const isAbort=(error:unknown)=>error instanceof DOMException&&error.name==='AbortError';
const errorText=(error:unknown)=>error instanceof ApiError&&error.status===404?'记录不存在或无权访问':error instanceof Error?error.message:'加载失败';
const enumLabel=(labels:Record<string,string>,value:string)=>labels[value]||'未知';
const filters=():AuditFilters=>({page:page.value,page_size:pageSize.value,...(category.value!=='all'?{category:category.value as AuditFilters['category']}:{}),...(source.value!=='all'?{source:source.value as AuditFilters['source']}:{}),...(status.value!=='all'?{status:status.value as AuditFilters['status']}:{}),...(risk.value!=='all'?{risk_level:risk.value as AuditFilters['risk_level']}:{}),...(action.value.trim()?{action:action.value.trim()}:{}),...(projectId.value.trim()?{project_id:projectId.value.trim()}:{}),...(userId.value.trim()?{user_id:userId.value.trim()}:{}),...(query.value.trim()?{query:query.value.trim()}:{}),...(occurredAfter.value?{occurred_after:occurredAfter.value}:{}),...(occurredBefore.value?{occurred_before:occurredBefore.value}:{})});
async function loadEvents(){const generation=++listGeneration;listController?.abort();const controller=new AbortController();listController=controller;loading.value=true;listError.value='';try{const result=await auditApi.list(filters(),controller.signal);if(generation!==listGeneration)return;events.value=result.items;total.value=result.total;summary.value=result.summary;}catch(error){if(generation!==listGeneration||isAbort(error))return;listError.value=errorText(error);}finally{if(generation===listGeneration){loading.value=false;if(listController===controller)listController=undefined;}}}
function applyFilters(){page.value=1;loadEvents();}
function changeDates(dates:[DateBoundary,DateBoundary]|null){occurredAfter.value=dates?.[0]?.startOf('day').toISOString()||'';occurredBefore.value=dates?.[1]?.endOf('day').toISOString()||'';applyFilters();}
function changePage(next:number,size:number){const changed=size!==pageSize.value;pageSize.value=size;page.value=changed?1:next;loadEvents();}
function cancelDrawer(){drawerGeneration++;for(const state of [detailRequest,relatedRequest]){state.controller?.abort();state.controller=undefined;state.generation++;}}
function requestResource<T>(state:typeof detailRequest,target:Resource<T>,request:(signal:AbortSignal)=>Promise<T>){state.controller?.abort();const controller=new AbortController(),generation=++state.generation,drawer=drawerGeneration,eventId=activeId.value;state.controller=controller;target.loading=true;target.error='';request(controller.signal).then(data=>{if(drawer===drawerGeneration&&eventId===activeId.value&&generation===state.generation&&state.controller===controller)target.data=data;}).catch(error=>{if(drawer===drawerGeneration&&eventId===activeId.value&&generation===state.generation&&!isAbort(error))target.error=errorText(error);}).finally(()=>{if(drawer===drawerGeneration&&generation===state.generation&&state.controller===controller){target.loading=false;state.controller=undefined;}});}
function retryDetail(){detail.value.data=null;requestResource(detailRequest,detail.value,signal=>auditApi.get(activeId.value,signal));}
function retryRelated(){related.value.data=null;requestResource(relatedRequest,related.value,signal=>auditApi.related(activeId.value,signal));}
function openEvent(id:string){cancelDrawer();activeId.value=id;drawerOpen.value=true;detail.value={data:null,loading:true,error:''};related.value={data:null,loading:true,error:''};retryDetail();retryRelated();}
function closeDrawer(){drawerOpen.value=false;activeId.value='';cancelDrawer();}
function openRun(runId:string){router.push({path:'/runs',query:{run_id:runId}});}
const sortedRelated=computed(()=>[...(related.value.data||[])].sort((a,b)=>{const av=String((a as AuditEventListItem&{created_at?:string}).created_at||a.occurred_at),bv=String((b as AuditEventListItem&{created_at?:string}).created_at||b.occurred_at);return av.localeCompare(bv)||a.id.localeCompare(b.id);}));
function statusColor(value:string){return ({started:'blue',succeeded:'green',failed:'red',cancelled:'default'} as Record<string,string>)[value];}
function riskColor(value:string){return ({low:'green',medium:'orange',high:'red',critical:'magenta'} as Record<string,string>)[value];}
function formatTime(value:string){const date=new Date(value);return Number.isNaN(date.getTime())?'-':date.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'});}
function safeJson(value:Record<string,unknown>){return JSON.stringify(value,null,2);}
onMounted(loadEvents);onBeforeUnmount(()=>{listGeneration++;listController?.abort();cancelDrawer();});
</script>

<style scoped>
.audit-page{display:grid;gap:16px;min-width:0}.audit-heading{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:16px 20px;background:#fff;border:1px solid #e1e9f2;border-left:4px solid #17856b;border-radius:8px}.heading-label{color:#17856b;font:700 10px Consolas,monospace;letter-spacing:0}.audit-heading h2{margin:2px 0 0;font-size:20px}.summary-strip{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));background:#fff;border:1px solid #e1e9f2;border-radius:8px}.summary-strip div{display:flex;justify-content:space-between;padding:13px 18px;border-right:1px solid #e8eef5}.summary-strip div:last-child{border:0}.summary-strip span{color:#667085}.summary-strip strong{color:#156b59;font-size:22px}.filter-bar{display:grid;grid-template-columns:repeat(4,minmax(120px,1fr));gap:10px}.filter-bar>*{min-width:0;width:100%}.table-shell{overflow-x:auto;background:#fff;border:1px solid #dfe8f1;border-radius:8px}table{width:100%;min-width:1120px;border-collapse:collapse;table-layout:fixed}th,td{overflow:hidden;padding:12px;border-bottom:1px solid #e8eef5;text-align:left;text-overflow:ellipsis;white-space:nowrap}th{color:#667085;background:#f7f9fc;font-size:12px}td{color:#344054;font-size:13px}td strong,td small,td code,td span{display:block;overflow:hidden;text-overflow:ellipsis}.run-link{max-width:100%;padding:0;border:0;color:#1677ff;background:none;overflow:hidden;text-overflow:ellipsis;cursor:pointer}footer{display:flex;align-items:center;justify-content:space-between;color:#667085}.details{display:grid;gap:22px}.details>section{min-width:0;padding-bottom:20px;border-bottom:1px solid #e8eef5}.details h3{font-size:15px}.details dl{display:grid;grid-template-columns:1fr 1fr;gap:10px}.details dl div{min-width:0}dt{color:#7b8798;font-size:12px}dd{margin:3px 0;overflow-wrap:anywhere}.summary{overflow-wrap:anywhere}pre{overflow:auto;max-width:100%;padding:10px;background:#f6f8fa;border:1px solid #e8eef5;border-radius:6px;font:12px/1.5 Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere}.trace{position:relative;display:grid;gap:0;margin:12px 0 0;padding:0;list-style:none}.trace::before{position:absolute;top:8px;bottom:8px;left:5px;width:2px;background:#d9e8e3;content:''}.trace-item{position:relative;display:grid;grid-template-columns:120px minmax(0,1fr);gap:14px;padding:0 0 18px 20px}.trace-item::before{position:absolute;top:5px;left:0;width:12px;height:12px;border:3px solid #17856b;border-radius:50%;background:#fff;content:''}.trace-item time,.trace-item span,.trace-item code{display:block;color:#7b8798;font-size:12px;overflow-wrap:anywhere}@media(max-width:1100px){.filter-bar{grid-template-columns:1fr 1fr}}@media(max-width:700px){.audit-heading{align-items:flex-start}.summary-strip{grid-template-columns:1fr 1fr}.summary-strip div:nth-child(2n){border-right:0}.summary-strip div:last-child{grid-column:1/-1}.filter-bar{grid-template-columns:1fr}footer{align-items:flex-start;flex-direction:column}.details dl{grid-template-columns:1fr}.trace-item{grid-template-columns:1fr;gap:3px}}
</style>
