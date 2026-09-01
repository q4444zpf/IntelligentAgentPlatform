<template>
  <main class="team-page">
    <header class="page-heading">
      <div><h1>多智能体团队</h1><p>管理项目内可发布、可复现的协同执行边界</p></div>
      <a-button v-if="canManage" type="primary" data-testid="team-create" @click="openCreate">新建团队</a-button>
    </header>

    <section class="filters" aria-label="团队筛选">
      <a-input v-model:value="search" allow-clear placeholder="搜索团队名称或描述" />
      <a-select v-model:value="lifecycle" :options="lifecycleOptions" aria-label="发布状态" />
      <a-select v-model:value="enabledFilter" :options="enabledOptions" aria-label="启用状态" />
      <span>{{ filteredTeams.length }} 个团队</span>
    </section>

    <a-alert v-if="loadError" type="error" show-icon :message="loadError" />
    <a-spin :spinning="loading">
      <div v-if="filteredTeams.length" class="team-table" role="table">
        <div class="team-row table-head" role="row"><span>团队</span><span>生命周期</span><span>主管 / 成员</span><span>更新时间</span><span>操作</span></div>
        <div v-for="team in filteredTeams" :key="team.id" class="team-row" role="row">
          <div class="team-name"><strong>{{ team.name }}</strong><small>{{ team.description || '未填写说明' }}</small><code>{{ team.id }}</code></div>
          <div><a-tag :color="team.published_version ? 'green' : 'default'">{{ team.published_version ? `已发布 v${team.published_version}` : '仅草稿' }}</a-tag><small>{{ team.enabled ? '已启用' : '已停用' }}</small></div>
          <div><strong>{{ agentName(team.supervisor?.agent_id) }}</strong><small>{{ team.member_count }} 个成员</small></div>
          <time>{{ formatTime(team.updated_at) }}</time>
          <div class="actions">
            <a-button data-testid="team-history" @click="openHistory(team)">版本</a-button>
            <a-button v-if="canManage" data-testid="team-edit" @click="openEdit(team)">编辑</a-button>
            <a-switch v-if="canManage" :checked="team.enabled" :disabled="!team.published_version" @change="toggleTeam(team, $event)" />
          </div>
        </div>
      </div>
      <a-empty v-else-if="!loading" description="当前项目暂无匹配团队" />
    </a-spin>

    <a-modal v-model:open="createOpen" title="新建团队" @ok="createTeam">
      <div class="form-stack"><label>团队名称<a-input v-model:value="createForm.name" maxlength="120" /></label><label>团队说明<a-input v-model:value="createForm.description" maxlength="500" /></label></div>
    </a-modal>

    <a-drawer v-model:open="drawerOpen" width="min(720px, 100vw)" :title="editorTitle">
      <template v-if="selectedTeam">
        <a-alert v-if="validationErrors.length" type="error" show-icon message="请修正团队定义"><ul><li v-for="error in validationErrors" :key="error">{{ error }}</li></ul></a-alert>
        <section v-if="draft && canManage && !historyOnly" class="editor-section">
          <h2>成员与职责</h2>
          <div class="form-grid">
            <label>主管智能体<a-select v-model:value="draft.supervisor.agent_id" :options="agentOptions" /></label>
            <label>主管职责<a-input v-model:value="draft.supervisor.responsibility" maxlength="500" /></label>
          </div>
          <div class="available-agents">可用智能体：<span v-for="agent in availableAgents" :key="agent.id">{{ agent.name }}</span></div>
          <div v-for="(member, index) in draft.members" :key="index" class="member-row">
            <a-select v-model:value="member.agent_id" :options="memberAgentOptions" aria-label="成员智能体" />
            <a-input v-model:value="member.responsibility" maxlength="500" placeholder="成员职责" />
            <a-button aria-label="移除成员" @click="draft.members.splice(index, 1)">移除</a-button>
          </div>
          <a-button @click="addMember">添加成员</a-button>
        </section>
        <section v-if="draft && canManage && !historyOnly" class="editor-section">
          <h2>运行边界</h2>
          <div class="limit-grid">
            <label>最大步骤<a-input-number v-model:value="draft.max_steps" :min="1" :max="100" /></label>
            <label>并行成员数<a-input-number v-model:value="draft.max_parallel_members" :min="1" :max="32" /></label>
            <label>超时秒数<a-input-number v-model:value="draft.timeout_seconds" :min="1" :max="86400" /></label>
            <label>失败策略<a-select v-model:value="draft.failure_strategy" :options="failureOptions" /></label>
          </div>
        </section>
        <section class="editor-section history-section">
          <h2>发布历史</h2>
          <div v-if="versions.length" class="version-list"><div v-for="version in versions" :key="version.id"><strong>版本 {{ version.version }}</strong><span>{{ formatTime(version.published_at) }}</span><code>{{ version.definition_digest?.slice(0, 12) }}</code></div></div>
          <a-empty v-else description="暂无已发布版本" />
        </section>
      </template>
      <template #footer><div v-if="canManage && !historyOnly" class="drawer-actions"><a-button @click="save(false)">保存草稿</a-button><a-button type="primary" data-testid="team-publish" @click="save(true)">保存并发布</a-button></div></template>
    </a-drawer>
  </main>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { message } from 'ant-design-vue';
import { agentsApi, type AgentInfo } from '@/api/agents';
import { teamsApi, type TeamDraft, type TeamSummary, type TeamVersionInfo } from '@/api/teams';
import { usePermissionStore } from '@/stores/permission';

const permissionStore = usePermissionStore();
const canManage = computed(() => permissionStore.isAdmin);
const teams = ref<TeamSummary[]>([]); const availableAgents = ref<AgentInfo[]>([]); const versions = ref<TeamVersionInfo[]>([]);
const loading = ref(true); const loadError = ref(''); const search = ref(''); const lifecycle = ref('all'); const enabledFilter = ref('all');
const createOpen = ref(false); const drawerOpen = ref(false); const historyOnly = ref(false); const selectedTeam = ref<TeamSummary | null>(null); const draft = ref<TeamDraft | null>(null); const validationErrors = ref<string[]>([]);
const createForm = reactive({ name: '', description: '' });
const lifecycleOptions = [{ value: 'all', label: '全部生命周期' }, { value: 'published', label: '已发布' }, { value: 'draft', label: '仅草稿' }];
const enabledOptions = [{ value: 'all', label: '全部启用状态' }, { value: 'enabled', label: '已启用' }, { value: 'disabled', label: '已停用' }];
const failureOptions = [{ value: 'fail_fast', label: '成员失败即终止' }, { value: 'continue_then_synthesize', label: '继续执行并汇总' }];
const filteredTeams = computed(() => teams.value.filter((team) => {
  const query = search.value.trim().toLowerCase();
  return (!query || `${team.name}${team.description}`.toLowerCase().includes(query))
    && (lifecycle.value === 'all' || (lifecycle.value === 'published') === Boolean(team.published_version))
    && (enabledFilter.value === 'all' || (enabledFilter.value === 'enabled') === team.enabled);
}));
const agentOptions = computed(() => availableAgents.value.filter((agent) => agent.enabled).map((agent) => ({ value: agent.id, label: agent.name })));
const memberAgentOptions = computed(() => agentOptions.value.filter((option) => option.value !== draft.value?.supervisor.agent_id));
const editorTitle = computed(() => historyOnly.value ? `${selectedTeam.value?.name || ''} · 版本历史` : `编辑 ${selectedTeam.value?.name || ''}`);

onMounted(load);
async function load() { loading.value = true; loadError.value = ''; try { [teams.value, availableAgents.value] = await Promise.all([teamsApi.list(), agentsApi.list()]); } catch (error) { loadError.value = error instanceof Error ? error.message : '团队目录加载失败'; } finally { loading.value = false; } }
function agentName(id?: string | null) { return availableAgents.value.find((agent) => agent.id === id)?.name || id || '未发布'; }
function formatTime(value?: string | null) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'; }
function openCreate() { createForm.name = ''; createForm.description = ''; createOpen.value = true; }
async function createTeam() { if (!createForm.name.trim()) { message.error('请输入团队名称'); return; } const created = await teamsApi.create({ name: createForm.name.trim(), description: createForm.description.trim() }); teams.value.push(created); createOpen.value = false; await openEdit(created); }
async function openEdit(team: TeamSummary) { selectedTeam.value = team; historyOnly.value = false; validationErrors.value = []; drawerOpen.value = true; const [savedDraft, history] = await Promise.all([teamsApi.getVersion(team.id, 0), teamsApi.listVersions(team.id)]); draft.value = structuredClone(savedDraft.definition); versions.value = history; }
async function openHistory(team: TeamSummary) { selectedTeam.value = team; historyOnly.value = true; draft.value = null; drawerOpen.value = true; versions.value = await teamsApi.listVersions(team.id); }
function addMember() { draft.value?.members.push({ agent_id: '', responsibility: '', tool_ids: [], skill_names: [], knowledge_source_ids: [] }); }
function validateDraft(value: TeamDraft) { const errors: string[] = []; if (!value.supervisor.agent_id) errors.push('请选择主管智能体'); if (!value.supervisor.responsibility.trim()) errors.push('请填写主管职责'); if (!value.members.length) errors.push('至少选择一个成员智能体'); value.members.forEach((member, index) => { if (!member.agent_id) errors.push(`请选择第 ${index + 1} 个成员智能体`); if (!member.responsibility.trim()) errors.push(`请填写第 ${index + 1} 个成员职责`); }); if (value.members.some((member) => member.agent_id === value.supervisor.agent_id)) errors.push('主管不能同时作为成员'); if (new Set(value.members.map((member) => member.agent_id)).size !== value.members.length) errors.push('成员智能体不能重复'); return errors; }
async function save(publish: boolean) { if (!draft.value || !selectedTeam.value) return; validationErrors.value = validateDraft(draft.value); if (validationErrors.value.length) return; const id = selectedTeam.value.id; const saved = await teamsApi.saveDraft(id, selectedTeam.value.draft_revision, draft.value); selectedTeam.value = saved; if (publish) { await teamsApi.publish(id); message.success('团队已发布'); } else message.success('草稿已保存'); drawerOpen.value = false; await load(); }
async function toggleTeam(team: TeamSummary, value: boolean) { const updated = await teamsApi.setEnabled(team.id, value); teams.value = teams.value.map((item) => item.id === updated.id ? updated : item); }
</script>

<style scoped>
.team-page { min-width: 0; color: #182536; }.page-heading,.filters,.actions,.drawer-actions { display: flex; align-items: center; }.page-heading { justify-content: space-between; gap: 20px; padding: 4px 0 18px; }.page-heading h1 { margin: 0; font-size: 24px; letter-spacing: 0; }.page-heading p { margin: 5px 0 0; color: #667085; }.filters { gap: 10px; padding: 12px 0; border-block: 1px solid #e4e9ef; }.filters :deep(.ant-input-affix-wrapper) { max-width: 360px; }.filters :deep(.ant-select) { width: 170px; }.filters > span { margin-left: auto; color: #667085; white-space: nowrap; }.team-table { margin-top: 12px; border-top: 1px solid #dfe5eb; }.team-row { display: grid; grid-template-columns: minmax(220px, 2fr) minmax(125px, .8fr) minmax(150px, 1fr) minmax(140px, .8fr) minmax(220px, 1fr); gap: 14px; min-height: 72px; padding: 12px 8px; align-items: center; border-bottom: 1px solid #e6ebf0; }.table-head { min-height: 40px; color: #667085; background: #f7f9fb; font-size: 12px; font-weight: 700; }.team-name,.team-row > div { min-width: 0; }.team-name strong,.team-name small,.team-name code,.team-row div > small { display: block; overflow-wrap: anywhere; }.team-name small,.team-row div > small { margin-top: 3px; color: #667085; }.team-name code { margin-top: 4px; color: #8b96a5; font-size: 11px; }.actions { flex-wrap: wrap; gap: 6px; }.form-stack,.editor-section,.version-list { display: grid; gap: 12px; }.form-stack label,.editor-section label { display: grid; gap: 6px; }.editor-section { padding: 0 0 20px; margin-bottom: 20px; border-bottom: 1px solid #e4e9ef; }.editor-section h2 { margin: 0; font-size: 16px; }.form-grid,.limit-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }.available-agents { color: #667085; font-size: 12px; }.available-agents span { margin-left: 8px; color: #344054; }.member-row { display: grid; grid-template-columns: minmax(150px, .8fr) minmax(180px, 1fr) auto; gap: 8px; }.version-list > div { display: grid; grid-template-columns: 1fr auto auto; gap: 10px; padding: 10px 0; border-bottom: 1px solid #edf0f3; }.version-list span,.version-list code { color: #667085; }.drawer-actions { justify-content: flex-end; gap: 8px; }
@media (max-width: 900px) { .team-row { grid-template-columns: minmax(180px, 1.5fr) 120px 140px auto; }.team-row > time { display: none; }.table-head span:nth-child(4) { display: none; } }
@media (max-width: 640px) { .page-heading,.filters { align-items: stretch; flex-direction: column; }.filters :deep(.ant-input-affix-wrapper),.filters :deep(.ant-select) { width: 100%; max-width: none; }.filters > span { margin-left: 0; }.team-row,.team-row.table-head { grid-template-columns: minmax(0, 1fr); }.table-head { display: none; }.team-row { gap: 8px; }.form-grid,.limit-grid,.member-row { grid-template-columns: minmax(0, 1fr); }.actions { justify-content: flex-start; }.version-list > div { grid-template-columns: 1fr; }.drawer-actions :deep(button) { min-width: 0; flex: 1; white-space: normal; } }
</style>
