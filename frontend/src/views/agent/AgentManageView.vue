<template>
  <div class="agent-page">
    <a-alert v-if="loadError" type="error" show-icon closable message="智能体服务暂时不可用" :description="loadError" />
    <section class="agent-heading">
      <div><span class="eyebrow">AGENT RUNTIME</span><h2>智能体管理</h2><p>配置独立运行上下文、模型、技能与控制确认策略</p></div>
      <a-space wrap>
        <a-input v-model:value="query" allow-clear class="search-input" placeholder="搜索名称或 ID"><template #prefix><SearchOutlined /></template></a-input>
        <a-select v-model:value="runtimeFilter" class="runtime-filter" :options="runtimeFilterOptions" />
        <a-tooltip title="刷新智能体"><a-button aria-label="刷新智能体" :loading="loading" @click="loadAgents"><template #icon><ReloadOutlined /></template></a-button></a-tooltip>
        <a-button type="primary" @click="openCreate"><template #icon><PlusOutlined /></template>创建智能体</a-button>
      </a-space>
    </section>

    <div class="summary-strip">
      <div><span>智能体</span><strong>{{ agents.length }}</strong></div>
      <div><span>已启用</span><strong>{{ enabledCount }}</strong></div>
      <div><span>绑定 Skill</span><strong>{{ boundSkillCount }}</strong></div>
      <div><span>需人工确认</span><strong>{{ approvalCount }}</strong></div>
    </div>

    <a-spin :spinning="loading && !agents.length">
      <div v-if="filteredAgents.length" class="agent-list">
        <article v-for="agent in filteredAgents" :key="agent.id" class="agent-row">
          <button class="pin-button" type="button" :aria-label="agent.pinned ? '取消置顶' : '置顶智能体'" :class="{ active: agent.pinned }" @click="pinAgent(agent)"><PushpinFilled v-if="agent.pinned" /><PushpinOutlined v-else /></button>
          <div class="runtime-icon" :class="agent.runtime_form"><DesktopOutlined v-if="agent.runtime_form === 'desktop'" /><GlobalOutlined v-else-if="agent.runtime_form === 'web'" /><RobotOutlined v-else /></div>
          <div class="agent-main">
            <div class="agent-title"><strong>{{ agent.name }}</strong><a-tag :color="runtimeColor(agent.runtime_form)">{{ runtimeLabel(agent.runtime_form) }}</a-tag><code>{{ agent.id }}</code></div>
            <p>{{ agent.description || '暂无说明' }}</p>
            <div class="binding-line"><span><ApiOutlined /> {{ modelLabel(agent) }}</span><span><ToolOutlined /> {{ agent.skill_names.length }} 个 Skill</span><span><FolderOutlined /> {{ compactPath(agent.workspace_dir) }}</span></div>
          </div>
          <div class="approval"><strong>{{ approvalLabel(agent.approval_policy) }}</strong><span>确认策略</span></div>
          <div class="agent-state"><span :class="['state-dot', { ready: agent.enabled }]" /><strong>{{ agent.enabled ? '可用' : '停用' }}</strong><span>{{ formatTime(agent.updated_at) }}</span></div>
          <div class="row-actions">
            <a-tooltip title="复制智能体"><a-button aria-label="复制智能体" @click="openCopy(agent)"><template #icon><CopyOutlined /></template></a-button></a-tooltip>
            <a-tooltip title="编辑配置"><a-button aria-label="编辑智能体" @click="openEdit(agent)"><template #icon><SettingOutlined /></template></a-button></a-tooltip>
            <a-switch :checked="agent.enabled" :loading="busyId === agent.id" checked-children="开" un-checked-children="关" @change="toggleAgent(agent)" />
            <a-popconfirm title="删除后将同时移除智能体工作空间，确定继续？" ok-text="删除" cancel-text="取消" @confirm="deleteAgent(agent)"><a-button danger aria-label="删除智能体"><template #icon><DeleteOutlined /></template></a-button></a-popconfirm>
          </div>
        </article>
      </div>
      <a-empty v-else :description="query || runtimeFilter !== 'all' ? '没有匹配的智能体' : '还没有智能体'"><a-button v-if="!query && runtimeFilter === 'all'" type="primary" @click="openCreate">创建第一个智能体</a-button></a-empty>
    </a-spin>

    <a-modal v-model:open="editorOpen" :title="editingId ? `配置 ${editingId}` : '创建智能体'" width="820px" :confirm-loading="saving" ok-text="保存" cancel-text="取消" @ok="saveAgent">
      <a-tabs v-model:activeKey="editorTab">
        <a-tab-pane key="basic" tab="基础配置">
          <a-form layout="vertical">
            <div class="form-grid two"><a-form-item label="智能体 ID" required :validate-status="formErrors.id ? 'error' : undefined" :help="formErrors.id || '小写字母开头，可使用数字、- 和 _。'"><a-input v-model:value="form.id" :disabled="Boolean(editingId)" placeholder="reservoir-dispatch" /></a-form-item><a-form-item label="显示名称" required :validate-status="formErrors.name ? 'error' : undefined" :help="formErrors.name"><a-input v-model:value="form.name" placeholder="水库调度智能体" /></a-form-item></div>
            <a-form-item label="说明"><a-textarea v-model:value="form.description" :rows="2" maxlength="500" show-count /></a-form-item>
            <a-form-item label="运行形态" required><a-segmented v-model:value="form.runtime_form" block :options="runtimeOptions" /></a-form-item>
            <a-alert show-icon type="info" :message="runtimeHint" />
            <div class="form-grid two spaced"><a-form-item label="语言"><a-select v-model:value="form.language" :options="languageOptions" /></a-form-item><a-form-item label="初始状态"><a-switch v-model:checked="form.enabled" checked-children="启用" un-checked-children="停用" /></a-form-item></div>
          </a-form>
        </a-tab-pane>
        <a-tab-pane key="capabilities" tab="模型与能力">
          <a-form layout="vertical">
            <div class="form-grid two"><a-form-item label="模型供应商"><a-select v-model:value="form.provider_id" allow-clear show-search :options="providerOptions" placeholder="使用平台默认模型" @change="onProviderChange" /></a-form-item><a-form-item label="模型"><a-select v-model:value="form.model" allow-clear show-search :disabled="!form.provider_id" :options="modelOptions" placeholder="选择模型" /></a-form-item></div>
            <section class="skill-picker">
              <div class="skill-picker-heading">
                <div><strong>绑定 Skill</strong><span>已选择 {{ form.skill_names.length }} / {{ enabledSkills.length }}</span></div>
                <a-space size="small"><a-button size="small" type="primary" @click="selectAllSkills">全选</a-button><a-button size="small" @click="selectImportedSkills">选择导入项</a-button><a-button size="small" @click="clearSkills">清空</a-button></a-space>
              </div>
              <div class="skill-picker-filter">
                <a-input v-model:value="skillQuery" allow-clear placeholder="搜索 Skill"><template #prefix><SearchOutlined /></template></a-input>
                <a-select v-model:value="skillTag" :options="skillTagOptions" />
              </div>
              <a-empty v-if="!visibleSkills.length" image="simple" description="没有可选择的 Skill" />
              <div v-else class="skill-picker-grid">
                <button v-for="skill in visibleSkills" :key="skill.name" type="button" class="skill-picker-card" :class="{ selected: form.skill_names.includes(skill.name) }" @click="toggleSkillSelection(skill.name)">
                  <span class="skill-check"><CheckOutlined v-if="form.skill_names.includes(skill.name)" /></span>
                  <span class="skill-card-copy"><strong>{{ skill.name }}</strong><small>{{ skill.description }}</small><span><a-tag v-for="tag in skill.tags.slice(0, 2)" :key="tag">{{ tag }}</a-tag><em>{{ skill.source === 'imported' ? 'ZIP 导入' : '平台创建' }}</em></span></span>
                </button>
              </div>
              <p class="skill-picker-help">智能体运行时只能调用已绑定且处于启用状态的技能。</p>
            </section>
          </a-form>
        </a-tab-pane>
        <a-tab-pane key="prompts" tab="提示词与安全">
          <a-form layout="vertical">
            <a-form-item label="系统提示词" extra="定义角色、职责、约束与输出标准。"><a-textarea v-model:value="form.system_prompt" :rows="7" placeholder="你是专业的水库调度智能体……" /></a-form-item>
            <a-form-item :label="`${runtimeLabel(form.runtime_form)} 上下文提示词`" :extra="runtimePromptExtra"><a-textarea v-model:value="form.context_prompt" :rows="4" :placeholder="runtimePromptPlaceholder" /></a-form-item>
            <a-form-item label="人工确认策略"><a-radio-group v-model:value="form.approval_policy"><a-radio value="never">无需确认</a-radio><a-radio value="control_commands">控制命令需确认</a-radio><a-radio value="always">每次工具调用均确认</a-radio></a-radio-group></a-form-item>
          </a-form>
        </a-tab-pane>
      </a-tabs>
    </a-modal>

    <a-modal v-model:open="copyOpen" title="复制智能体" :confirm-loading="copying" ok-text="创建副本" cancel-text="取消" @ok="copyAgent">
      <a-alert type="info" show-icon :message="`复制 ${copySource?.name || ''} 的模型、提示词和运行配置`" />
      <a-form layout="vertical" class="copy-form"><a-form-item label="新智能体 ID" required><a-input v-model:value="copyForm.id" placeholder="agent-copy" /></a-form-item><a-form-item label="显示名称" required><a-input v-model:value="copyForm.name" /></a-form-item><a-form-item><a-checkbox v-model:checked="copyForm.copy_skills">复制 Skill 绑定</a-checkbox></a-form-item></a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { message } from 'ant-design-vue';
import { ApiOutlined, CheckOutlined, CopyOutlined, DeleteOutlined, DesktopOutlined, FolderOutlined, GlobalOutlined, PlusOutlined, PushpinFilled, PushpinOutlined, ReloadOutlined, RobotOutlined, SearchOutlined, SettingOutlined, ToolOutlined } from '@ant-design/icons-vue';
import { agentsApi, type AgentApprovalPolicy, type AgentInfo, type AgentRuntimeForm } from '@/api/agents';
import { modelProviderApi, type ApiProvider } from '@/api/modelProviders';
import { skillsApi, type SkillInfo } from '@/api/skills';

const emptyForm = () => ({ id: '', name: '', description: '', runtime_form: 'common' as AgentRuntimeForm, language: 'zh-CN' as 'zh-CN' | 'en-US', provider_id: '', model: '', system_prompt: '', context_prompt: '', approval_policy: 'control_commands' as AgentApprovalPolicy, skill_names: [] as string[], enabled: true });
const agents = ref<AgentInfo[]>([]); const providers = ref<ApiProvider[]>([]); const skills = ref<SkillInfo[]>([]); const loading = ref(false); const loadError = ref(''); const query = ref(''); const runtimeFilter = ref('all'); const busyId = ref('');
const editorOpen = ref(false); const editorTab = ref('basic'); const editingId = ref(''); const saving = ref(false); const form = reactive(emptyForm()); const formErrors = reactive({ id: '', name: '' });
const skillQuery = ref(''); const skillTag = ref('all');
const copyOpen = ref(false); const copying = ref(false); const copySource = ref<AgentInfo>(); const copyForm = reactive({ id: '', name: '', copy_skills: true });
const runtimeFilterOptions = [{ label: '全部形态', value: 'all' }, { label: 'Web', value: 'web' }, { label: 'Desktop', value: 'desktop' }, { label: 'Common', value: 'common' }]; const runtimeOptions = [{ label: 'Web 页面', value: 'web' }, { label: '桌面客户端', value: 'desktop' }, { label: '通用服务', value: 'common' }]; const languageOptions = [{ label: '简体中文', value: 'zh-CN' }, { label: 'English', value: 'en-US' }];
const enabledCount = computed(() => agents.value.filter((item) => item.enabled).length); const boundSkillCount = computed(() => new Set(agents.value.flatMap((item) => item.skill_names)).size); const approvalCount = computed(() => agents.value.filter((item) => item.approval_policy !== 'never').length);
const filteredAgents = computed(() => { const term = query.value.trim().toLowerCase(); return agents.value.filter((item) => (!term || `${item.name} ${item.id} ${item.description}`.toLowerCase().includes(term)) && (runtimeFilter.value === 'all' || item.runtime_form === runtimeFilter.value)); });
const providerOptions = computed(() => providers.value.filter((item) => item.enabled && item.configured).map((item) => ({ label: item.name, value: item.id }))); const activeProvider = computed(() => providers.value.find((item) => item.id === form.provider_id)); const modelOptions = computed(() => (activeProvider.value?.models || []).filter((item) => item.enabled).map((item) => ({ label: item.name, value: item.id }))); const enabledSkills = computed(() => skills.value.filter((item) => item.enabled)); const skillTagOptions = computed(() => [{ label: '全部标签', value: 'all' }, ...Array.from(new Set(enabledSkills.value.flatMap((item) => item.tags))).sort((a, b) => a.localeCompare(b, 'zh-CN')).map((tag) => ({ label: tag, value: tag }))]); const visibleSkills = computed(() => { const term = skillQuery.value.trim().toLowerCase(); return enabledSkills.value.filter((item) => (!term || `${item.name} ${item.description}`.toLowerCase().includes(term)) && (skillTag.value === 'all' || item.tags.includes(skillTag.value))); });
const runtimeHint = computed(() => form.runtime_form === 'web' ? 'Web 智能体接收当前页面、业务对象和用户会话上下文。' : form.runtime_form === 'desktop' ? '桌面智能体可接收本地文件、客户端状态和通知上下文，控制动作必须支持人工确认。' : '通用智能体面向 API、工作流和后台任务，不依赖特定终端上下文。'); const runtimePromptExtra = computed(() => form.runtime_form === 'web' ? '说明如何使用页面路由、选中对象和会话信息。' : form.runtime_form === 'desktop' ? '说明如何使用本地文件、客户端状态与通知，并限制高风险操作。' : '说明 API 或工作流传入上下文的解释方式。'); const runtimePromptPlaceholder = computed(() => form.runtime_form === 'web' ? '结合当前页面与选中的业务对象回答。' : form.runtime_form === 'desktop' ? '优先读取用户确认的本地文件；控制命令执行前请求确认。' : '根据任务输入和工作流上下文完成处理。');
async function loadAgents() { loading.value = true; loadError.value = ''; try { const [agentData, providerData, skillData] = await Promise.all([agentsApi.list(), modelProviderApi.list(), skillsApi.list()]); agents.value = agentData; providers.value = providerData; skills.value = skillData; } catch (error) { loadError.value = error instanceof Error ? error.message : '加载失败'; } finally { loading.value = false; } }
function resetForm() { Object.assign(form, emptyForm()); Object.assign(formErrors, { id: '', name: '' }); skillQuery.value = ''; skillTag.value = 'all'; }
function openCreate() { resetForm(); editingId.value = ''; editorTab.value = 'basic'; editorOpen.value = true; }
function openEdit(agent: AgentInfo) { resetForm(); editingId.value = agent.id; Object.assign(form, agent); editorTab.value = 'basic'; editorOpen.value = true; }
function validateForm() { Object.assign(formErrors, { id: '', name: '' }); if (!/^[a-z][a-z0-9_-]{0,63}$/.test(form.id)) formErrors.id = '请输入有效的智能体 ID'; if (!form.name.trim()) formErrors.name = '请输入显示名称'; if (formErrors.id || formErrors.name) editorTab.value = 'basic'; return !formErrors.id && !formErrors.name; }
async function saveAgent() { if (!validateForm()) return; saving.value = true; try { const { id, ...payload } = form; if (editingId.value) await agentsApi.update(editingId.value, payload); else await agentsApi.create({ id, ...payload }); message.success(editingId.value ? '智能体配置已更新' : '智能体已创建'); editorOpen.value = false; await loadAgents(); } catch (error) { message.error(error instanceof Error ? error.message : '保存失败'); } finally { saving.value = false; } }
async function toggleAgent(agent: AgentInfo) { busyId.value = agent.id; try { await agentsApi.toggle(agent.id, !agent.enabled); await loadAgents(); } catch (error) { message.error(error instanceof Error ? error.message : '切换状态失败'); } finally { busyId.value = ''; } }
async function pinAgent(agent: AgentInfo) { try { await agentsApi.pin(agent.id, !agent.pinned); await loadAgents(); } catch (error) { message.error(error instanceof Error ? error.message : '置顶失败'); } }
async function deleteAgent(agent: AgentInfo) { try { await agentsApi.remove(agent.id); message.success('智能体已删除'); await loadAgents(); } catch (error) { message.error(error instanceof Error ? error.message : '删除失败'); } }
function openCopy(agent: AgentInfo) { copySource.value = agent; Object.assign(copyForm, { id: `${agent.id}-copy`, name: `${agent.name}副本`, copy_skills: true }); copyOpen.value = true; }
async function copyAgent() { if (!copySource.value || !/^[a-z][a-z0-9_-]{0,63}$/.test(copyForm.id) || !copyForm.name.trim()) { message.error('请输入有效的副本 ID 和名称'); return; } copying.value = true; try { await agentsApi.copy(copySource.value.id, copyForm); message.success('智能体副本已创建，默认处于停用状态'); copyOpen.value = false; await loadAgents(); } catch (error) { message.error(error instanceof Error ? error.message : '复制失败'); } finally { copying.value = false; } }
function onProviderChange() { if (!modelOptions.value.some((item) => item.value === form.model)) form.model = ''; }
function toggleSkillSelection(name: string) { form.skill_names = form.skill_names.includes(name) ? form.skill_names.filter((item) => item !== name) : [...form.skill_names, name]; }
function selectAllSkills() { form.skill_names = enabledSkills.value.map((item) => item.name); }
function selectImportedSkills() { form.skill_names = Array.from(new Set([...form.skill_names, ...enabledSkills.value.filter((item) => item.source === 'imported').map((item) => item.name)])); }
function clearSkills() { form.skill_names = []; }
function runtimeLabel(value: AgentRuntimeForm) { return value === 'web' ? 'Web' : value === 'desktop' ? 'Desktop' : 'Common'; } function runtimeColor(value: AgentRuntimeForm) { return value === 'web' ? 'blue' : value === 'desktop' ? 'purple' : 'cyan'; } function approvalLabel(value: AgentApprovalPolicy) { return value === 'never' ? '无需确认' : value === 'always' ? '全部确认' : '控制命令'; } function modelLabel(agent: AgentInfo) { return agent.provider_id && agent.model ? `${agent.provider_id} / ${agent.model}` : '平台默认模型'; } function compactPath(value: string) { const parts = value.replace(/\\/g, '/').split('/'); return parts.slice(-2).join('/'); } function formatTime(value: string) { return new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }); }
onMounted(loadAgents);
</script>

<style scoped>
.agent-page { display: grid; gap: 16px; } .agent-heading { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 18px 20px; background: #fff; border: 1px solid #e1e9f2; border-radius: 8px; } .eyebrow { color: #356d9f; font: 700 10px Consolas, monospace; } .agent-heading h2 { margin: 3px 0 0; font-size: 20px; } .agent-heading p { margin: 5px 0 0; color: #667085; } .search-input { width: 235px; } .runtime-filter { width: 126px; }
.summary-strip { display: grid; grid-template-columns: repeat(4, 1fr); background: #fff; border: 1px solid #e1e9f2; border-radius: 8px; } .summary-strip div { display: flex; align-items: baseline; justify-content: space-between; padding: 14px 20px; border-right: 1px solid #e8eef5; } .summary-strip div:last-child { border-right: 0; } .summary-strip span { color: #667085; } .summary-strip strong { color: #173f67; font-size: 23px; }
.agent-list { display: grid; gap: 10px; } .agent-row { display: grid; grid-template-columns: 24px 44px minmax(300px, 1fr) 90px 125px auto; align-items: center; gap: 13px; min-height: 118px; padding: 15px 17px; background: #fff; border: 1px solid #dfe8f1; border-radius: 8px; transition: border-color .2s, box-shadow .2s; } .agent-row:hover { border-color: #a8c6e3; box-shadow: 0 5px 16px rgb(34 78 120 / 7%); } .pin-button { padding: 0; color: #98a2b3; background: transparent; border: 0; cursor: pointer; } .pin-button.active { color: #d48b16; } .runtime-icon { display: grid; width: 42px; height: 42px; place-items: center; color: #1768a8; background: #e9f3fb; border-radius: 6px; font-size: 20px; } .runtime-icon.desktop { color: #6842a5; background: #f1ecfa; } .runtime-icon.common { color: #087e8b; background: #e5f7f7; }
.agent-title { display: flex; align-items: center; gap: 7px; } .agent-title strong { font-size: 15px; } .agent-title code { color: #7b8798; font-size: 12px; } .agent-main p { margin: 7px 0; color: #596579; font-size: 13px; } .binding-line { display: flex; flex-wrap: wrap; gap: 14px; color: #7b8798; font-size: 12px; } .approval, .agent-state { display: flex; flex-direction: column; gap: 4px; } .approval strong, .agent-state strong { font-size: 13px; } .approval span, .agent-state span { color: #7b8798; font-size: 12px; } .state-dot { width: 7px; height: 7px; background: #98a2b3; border-radius: 50%; } .state-dot.ready { background: #12a36d; box-shadow: 0 0 0 3px #dff5eb; } .row-actions { display: flex; align-items: center; gap: 8px; }
.form-grid { display: grid; gap: 14px; } .form-grid.two { grid-template-columns: 1fr 1fr; } .form-grid.spaced { align-items: start; margin-top: 16px; } .copy-form { margin-top: 16px; }
.skill-picker { margin-top: 8px; } .skill-picker-heading, .skill-picker-filter { display: flex; align-items: center; justify-content: space-between; gap: 12px; } .skill-picker-heading > div { display: flex; align-items: baseline; gap: 10px; } .skill-picker-heading span { color: #667085; font-size: 12px; } .skill-picker-filter { margin: 12px 0 10px; } .skill-picker-filter .ant-input-affix-wrapper { flex: 1; } .skill-picker-filter .ant-select { width: 150px; } .skill-picker-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; max-height: 310px; padding: 2px; overflow-y: auto; } .skill-picker-card { position: relative; display: grid; grid-template-columns: 22px 1fr; gap: 8px; min-height: 92px; padding: 12px; text-align: left; background: #fff; border: 1px solid #dce5ee; border-radius: 6px; cursor: pointer; } .skill-picker-card:hover { border-color: #8eb8dc; } .skill-picker-card.selected { background: #f0f7fd; border-color: #2778ba; box-shadow: inset 3px 0 #2778ba; } .skill-check { display: grid; width: 18px; height: 18px; place-items: center; color: #fff; background: #fff; border: 1px solid #b8c5d3; border-radius: 4px; } .skill-picker-card.selected .skill-check { background: #2778ba; border-color: #2778ba; } .skill-card-copy { min-width: 0; } .skill-card-copy strong, .skill-card-copy small { display: block; } .skill-card-copy strong { overflow: hidden; color: #172033; font: 700 13px Consolas, monospace; text-overflow: ellipsis; white-space: nowrap; } .skill-card-copy small { display: -webkit-box; min-height: 34px; margin: 4px 0 7px; overflow: hidden; color: #667085; line-height: 1.4; -webkit-box-orient: vertical; -webkit-line-clamp: 2; } .skill-card-copy > span { display: flex; align-items: center; min-height: 22px; } .skill-card-copy em { margin-left: auto; color: #8a96a6; font-size: 11px; font-style: normal; } .skill-picker-help { margin: 8px 0 0; color: #7b8798; font-size: 12px; }
@media (max-width: 1150px) { .agent-heading { align-items: flex-start; flex-direction: column; } .agent-row { grid-template-columns: 24px 44px 1fr auto; } .approval, .agent-state { display: none; } }
@media (max-width: 700px) { .agent-heading :deep(.ant-space) { width: 100%; } .search-input { width: 100%; } .summary-strip { grid-template-columns: 1fr 1fr; } .summary-strip div:nth-child(2) { border-right: 0; } .summary-strip div:nth-child(-n+2) { border-bottom: 1px solid #e8eef5; } .agent-row { grid-template-columns: 20px 40px 1fr; } .agent-main { min-width: 0; } .agent-title { align-items: flex-start; flex-wrap: wrap; } .binding-line span:last-child { display: none; } .row-actions { grid-column: 1 / -1; justify-content: flex-end; } .form-grid.two, .skill-picker-grid { grid-template-columns: 1fr; } .skill-picker-heading { align-items: flex-start; flex-direction: column; } }
</style>
