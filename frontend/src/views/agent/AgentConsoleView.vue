<template>
  <main class="conversation-page" :class="{ 'focus-mode': isFocusMode }">
    <header class="conversation-topbar">
      <div class="conversation-brand">
        <button v-if="isFocusMode" class="icon-button back-button" title="返回平台" @click="router.push('/chat')">
          <ArrowLeftOutlined />
        </button>
        <span class="brand-pulse"><CloudServerOutlined /></span>
        <div>
          <strong>水利智能协同中心</strong>
          <small>北江流域防洪调度项目 · {{ conversationStore.error || '会话服务已连接' }}</small>
        </div>
      </div>
      <div class="topbar-actions">
        <span class="runtime-state"><i /> {{ runtimeStatusLabel(conversationStore.activeRun?.status) }}</span>
        <button v-if="!isFocusMode" class="quiet-button" @click="router.push('/chat/focus')">
          <ExpandOutlined /> 独立页面
        </button>
        <button v-else class="quiet-button" @click="router.push('/chat')">
          <CompressOutlined /> 返回平台
        </button>
      </div>
    </header>

    <div class="mobile-tabs" role="tablist">
      <button :class="{ active: mobilePanel === 'history' }" @click="mobilePanel = 'history'">会话</button>
      <button :class="{ active: mobilePanel === 'chat' }" @click="mobilePanel = 'chat'">对话</button>
      <button :class="{ active: mobilePanel === 'context' }" @click="mobilePanel = 'context'">上下文</button>
    </div>

    <div class="conversation-layout">
      <aside class="history-panel" :class="{ 'mobile-active': mobilePanel === 'history' }">
        <button class="new-chat-button" @click="newConversation"><PlusOutlined /> 新建对话</button>
        <label class="search-box">
          <SearchOutlined />
          <input v-model="historySearch" placeholder="搜索历史会话" />
        </label>
        <div class="history-heading"><span>最近会话</span><HistoryOutlined /></div>
        <div class="history-list">
          <button
            v-for="session in filteredSessions"
            :key="session.id"
            :class="{ active: conversationStore.activeConversationId === session.id }"
            @click="selectSession(session.id)"
          >
            <span class="history-icon"><MessageOutlined /></span>
            <span class="history-copy">
              <strong>{{ session.title }}</strong>
              <small>{{ session.summary }}</small>
              <em>{{ session.time }} · {{ session.mode }}</em>
            </span>
            <MoreOutlined />
          </button>
        </div>
        <button class="archive-button"><FolderOpenOutlined /> 查看归档会话 <RightOutlined /></button>
      </aside>

      <section class="chat-workspace" :class="{ 'mobile-active': mobilePanel === 'chat' }">
        <div class="chat-toolbar">
          <div class="mode-switch" aria-label="协作模式">
            <button :class="{ active: mode === 'single' }" @click="setMode('single')"><UserOutlined /> 单智能体</button>
            <button :class="{ active: mode === 'team' }" @click="setMode('team')"><TeamOutlined /> 多智能体协同</button>
          </div>
          <div class="toolbar-selectors">
            <label class="selector-field actor-field">
              <span>执行主体</span>
              <a-select v-if="mode === 'single'" v-model:value="selectedAgentId" class="actor-select" :options="agentOptions" />
              <a-select v-else v-model:value="selectedTeamId" class="actor-select" :options="teamOptions" />
            </label>
            <label class="selector-field knowledge-field">
              <span>知识库</span>
              <a-select v-model:value="selectedKnowledgeIds" mode="multiple" class="knowledge-select" :max-tag-count="1" :options="knowledgeOptions" placeholder="选择知识库" />
            </label>
            <label class="selector-field resource-field">
              <span>业务资源</span>
              <a-select v-model:value="selectedResourceIds" mode="multiple" class="resource-select" :max-tag-count="1" :options="resourceOptions" placeholder="选择业务资源" />
            </label>
          </div>
        </div>

        <div class="conversation-summary">
          <div class="summary-avatar" :class="mode"><TeamOutlined v-if="mode === 'team'" /><RadarChartOutlined v-else /></div>
          <div>
            <strong>{{ activeActorName }}</strong>
            <p>{{ mode === 'team' ? '统筹预报分析、GIS 空间研判和调度方案生成' : '面向水情检索、趋势分析与调度建议的专业智能体' }}</p>
          </div>
          <span>{{ runtimeStatusLabel(conversationStore.activeRun?.status) }}</span>
        </div>

        <div ref="message-list" class="message-stream" aria-live="polite">
          <div v-if="messages.length" class="date-divider"><span>当前会话</span></div>
          <article v-for="message in messages" :key="message.id" class="message-row" :class="message.role">
            <div class="message-avatar">{{ message.role === 'user' ? '张' : message.avatar }}</div>
            <div class="message-content">
              <div class="message-meta"><strong>{{ message.author }}</strong><span>{{ message.time }}</span></div>
              <div class="message-bubble">{{ message.content }}</div>
            </div>
          </article>
          <div v-if="isRunActive(conversationStore.activeRun?.status)" class="message-row agent">
            <div class="message-avatar">协</div>
            <div class="thinking"><i /><i /><i /><span>{{ runtimeStatusLabel(conversationStore.activeRun.status) }}</span></div>
          </div>
        </div>

        <div class="suggestion-row">
          <button v-for="prompt in suggestedPrompts" :key="prompt" @click="input = prompt">{{ prompt }}</button>
        </div>
        <div class="composer-shell">
          <textarea v-model="input" maxlength="2000" placeholder="向智能体描述任务，输入 @ 可指定团队成员…" @keydown.enter.exact.prevent="sendMessage" />
          <div class="composer-tools">
            <div>
              <button title="上传文件"><PaperClipOutlined /></button>
              <button title="指定团队成员" @click="input += '@'">@</button>
              <span>Enter 发送 · Shift + Enter 换行</span>
            </div>
            <button class="send-button" :disabled="!input.trim() || conversationStore.sending" title="发送" @click="sendMessage"><SendOutlined /></button>
          </div>
        </div>
      </section>

      <aside class="context-panel" :class="{ 'mobile-active': mobilePanel === 'context' }">
        <section>
          <header><span>运行上下文</span><SlidersOutlined /></header>
          <dl>
            <div><dt>当前项目</dt><dd>北江流域防洪调度</dd></div>
            <div><dt>运行环境</dt><dd><i class="green-dot" /> 生产演练环境</dd></div>
            <div><dt>空间范围</dt><dd>飞来峡 - 清远河段</dd></div>
            <div><dt>数据时效</dt><dd>2026-07-24 09:40</dd></div>
          </dl>
        </section>
        <section v-if="mode === 'team'">
          <header><span>协同成员</span><b>4</b></header>
          <div class="member-list">
            <div v-for="member in teamMembers" :key="member.name">
              <span :style="{ background: member.color }">{{ member.name.slice(0, 1) }}</span>
              <div><strong>{{ member.name }}</strong><small>{{ member.role }}</small></div>
              <i />
            </div>
          </div>
        </section>
        <section>
          <header><span>已连接知识库</span><DatabaseOutlined /></header>
          <div class="kb-list">
            <div v-for="kb in selectedKnowledge" :key="kb.value"><BookOutlined /><span><strong>{{ kb.label }}</strong><small>已索引 · 可引用溯源</small></span></div>
          </div>
          <button class="manage-context"><PlusOutlined /> 添加知识库</button>
        </section>
        <section>
          <header><span>已挂载业务资源</span><b>{{ selectedResources.length }}</b></header>
          <div class="resource-list">
            <div v-for="resource in selectedResources" :key="resource.value">
              <span>{{ resource.shortType }}</span>
              <div><strong>{{ resource.label }}</strong><small>{{ resource.type }} · {{ resource.detail }}</small></div>
            </div>
          </div>
          <button class="manage-context"><PlusOutlined /> 添加业务资源</button>
        </section>
        <section class="safety-section">
          <header><span>执行策略</span><SafetyCertificateOutlined /></header>
          <p><i /> 查询与分析自动执行</p>
          <p><i class="warning" /> 控制指令需人工确认</p>
          <small>所有工具调用写入项目审计日志</small>
        </section>
      </aside>
    </div>
  </main>
</template>

<script setup lang="ts">
import {
  ArrowLeftOutlined, BookOutlined, CloudServerOutlined, CompressOutlined,
  DatabaseOutlined, ExpandOutlined, FolderOpenOutlined, HistoryOutlined,
  MessageOutlined, MoreOutlined, PaperClipOutlined, PlusOutlined, RadarChartOutlined, RightOutlined,
  SafetyCertificateOutlined, SearchOutlined, SendOutlined, SlidersOutlined, TeamOutlined, UserOutlined,
} from '@ant-design/icons-vue';
import { computed, nextTick, onMounted, ref, useTemplateRef, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { isRunActive, runtimeStatusLabel } from '@/features/chat/runtimeStatus';
import { useConversationStore } from '@/stores/conversations';

type ChatMode = 'single' | 'team';
interface ChatMessage { id: string; role: 'user' | 'agent'; author: string; avatar: string; time: string; content: string }

const route = useRoute();
const router = useRouter();
const conversationStore = useConversationStore();
const isFocusMode = computed(() => route.meta.focus === true);
const mobilePanel = ref<'history' | 'chat' | 'context'>('chat');
const mode = ref<ChatMode>('team');
const selectedAgentId = ref('flood');
const selectedTeamId = ref('flood-team');
const selectedKnowledgeIds = ref<string[]>(['dispatch', 'regulation']);
const selectedResourceIds = ref<string[]>(['beijiang-topology', 'qingyuan-dem']);
const historySearch = ref('');
const input = ref('');
const messageList = useTemplateRef<HTMLElement>('message-list');

const agentOptions = [
  { value: 'flood', label: '防洪调度智能体' }, { value: 'forecast', label: '洪水预报智能体' }, { value: 'gis', label: 'GIS 空间分析智能体' },
];
const teamOptions = [
  { value: 'flood-team', label: '北江防洪协同团队' }, { value: 'reservoir-team', label: '水库群调度团队' }, { value: 'temporary', label: '临时研判团队' },
];
const knowledgeOptions = [
  { value: 'dispatch', label: '北江防洪调度规程' }, { value: 'regulation', label: '飞来峡水库运行资料' }, { value: 'history', label: '历史洪水案例库' },
];
const resourceOptions = [
  { value: 'beijiang-topology', label: '北江流域防洪调度拓扑', type: '工程拓扑', shortType: '拓', detail: 'v1.3.2 · 223 个对象' },
  { value: 'feilaixia-topology', label: '飞来峡水库工程拓扑', type: '工程拓扑', shortType: '拓', detail: 'v2.1.0 · 46 个对象' },
  { value: 'qingyuan-dem', label: '清远河段 DEM', type: 'DEM 数据', shortType: 'DEM', detail: '5 m · 2026-07-23' },
  { value: 'beijiang-imagery', label: '北江流域正射影像', type: '遥感影像', shortType: '影', detail: '0.5 m · 2026-06' },
  { value: 'river-section', label: '清远河段断面数据', type: '水利断面', shortType: '断', detail: '84 个断面' },
];
const selectedKnowledge = computed(() => knowledgeOptions.filter((item) => selectedKnowledgeIds.value.includes(item.value)));
const selectedResources = computed(() => resourceOptions.filter((item) => selectedResourceIds.value.includes(item.value)));
const activeActorName = computed(() => (mode.value === 'team' ? teamOptions : agentOptions).find((item) => item.value === (mode.value === 'team' ? selectedTeamId.value : selectedAgentId.value))?.label || '智能体');
const filteredSessions = computed(() => conversationStore.conversations
  .map((item) => ({ id: item.id, title: item.title, summary: '项目会话', time: formatTime(item.updated_at), mode: '持久化' }))
  .filter((item) => `${item.title}${item.summary}`.includes(historySearch.value.trim())));
const teamMembers = [
  { name: '洪水预报智能体', role: '流量预测与不确定性分析', color: '#2563eb' },
  { name: 'GIS 分析智能体', role: '淹没范围与沿程剖面', color: '#0891b2' },
  { name: '调度优化智能体', role: '方案推演与约束校核', color: '#16a47a' },
  { name: '报告生成智能体', role: '结论汇总与成果编制', color: '#d58b16' },
];
const suggestedPrompts = ['对比三套预泄方案', '生成沿程水位过程线', '研判未来 24 小时洪峰'];
const messages = computed<ChatMessage[]>(() => conversationStore.messages.map((message) => ({
  id: message.id,
  role: message.role === 'user' ? 'user' : 'agent',
  author: message.role === 'user' ? '当前用户' : activeActorName.value,
  avatar: message.role === 'user' ? '用' : mode.value === 'team' ? '协' : '智',
  time: formatTime(message.created_at),
  content: message.content,
})));

watch(messages, async () => { await nextTick(); messageList.value?.scrollTo({ top: messageList.value.scrollHeight, behavior: 'smooth' }); }, { deep: true });
onMounted(() => conversationStore.loadConversations());

function formatTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}
function setMode(value: ChatMode) { mode.value = value; }
async function selectSession(id: string) { await conversationStore.selectConversation(id); mobilePanel.value = 'chat'; }
function newConversation() { conversationStore.startNewConversation(); input.value = ''; mobilePanel.value = 'chat'; }
async function sendMessage() {
  const content = input.value.trim();
  if (!content || conversationStore.sending) return;
  const actorType = mode.value === 'team' ? 'team' : 'agent';
  const actorId = mode.value === 'team' ? selectedTeamId.value : selectedAgentId.value;
  await conversationStore.sendMessage(content, actorType, actorId);
  input.value = '';
}
</script>

<style scoped>
.conversation-page { --line: #dce7ef; --muted: #728797; --ink: #183047; min-width: 0; min-height: calc(100vh - 103px); min-height: calc(100dvh - 103px); overflow: hidden; color: var(--ink); background: #fff; border: 1px solid var(--line); border-radius: 7px; box-shadow: 0 8px 24px rgb(42 79 111 / 6%); }
.conversation-page.focus-mode { min-height: 100vh; border: 0; border-radius: 0; }
.conversation-topbar { display: flex; height: 62px; align-items: center; justify-content: space-between; padding: 0 18px; background: #fbfdff; border-bottom: 1px solid var(--line); }
.conversation-brand,.topbar-actions,.chat-toolbar,.toolbar-selectors,.conversation-summary,.message-meta,.composer-tools,.composer-tools > div { display: flex; align-items: center; }
.conversation-brand { gap: 10px; }.conversation-brand strong,.conversation-brand small { display: block; }.conversation-brand strong { font-size: 14px; }.conversation-brand small { margin-top: 3px; color: var(--muted); font-size: 10px; }
.brand-pulse { display: grid; width: 34px; height: 34px; place-items: center; color: #fff; background: #2563eb; border-radius: 5px; }.icon-button,.quiet-button { border: 1px solid var(--line); background: #fff; cursor: pointer; }.icon-button { width: 32px; height: 32px; border-radius: 5px; }.back-button { margin-right: 2px; }.topbar-actions { gap: 10px; }.runtime-state { color: #4e6b60; font-size: 11px; }.runtime-state i,.green-dot { display: inline-block; width: 7px; height: 7px; margin-right: 5px; background: #16a47a; border-radius: 50%; }.quiet-button { height: 32px; padding: 0 11px; color: #436176; border-radius: 5px; }
.conversation-layout { display: grid; grid-template-columns: 250px minmax(480px, 1fr) 280px; height: calc(100vh - 166px); height: calc(100dvh - 166px); min-height: 560px; }.focus-mode .conversation-layout { height: calc(100vh - 62px); height: calc(100dvh - 62px); }.history-panel,.context-panel { min-width: 0; padding: 14px; background: #f9fcfe; }.history-panel { display: flex; flex-direction: column; border-right: 1px solid var(--line); }.context-panel { overflow-y: auto; border-left: 1px solid var(--line); }
.new-chat-button { width: 100%; height: 38px; color: #fff; background: #2563eb; border: 0; border-radius: 5px; cursor: pointer; font-weight: 700; }.search-box { display: flex; height: 34px; align-items: center; gap: 7px; margin: 12px 0 16px; padding: 0 9px; color: #8da0ad; background: #fff; border: 1px solid var(--line); border-radius: 5px; }.search-box input { width: 100%; min-width: 0; border: 0; outline: 0; color: var(--ink); background: transparent; font-size: 11px; }.history-heading { display: flex; justify-content: space-between; margin-bottom: 7px; color: #8294a1; font-size: 10px; font-weight: 700; }.history-list { display: grid; gap: 3px; overflow-y: auto; }.history-list button { display: grid; grid-template-columns: 25px 1fr 14px; gap: 7px; padding: 9px 7px; color: #7b8f9d; background: transparent; border: 1px solid transparent; border-radius: 5px; cursor: pointer; text-align: left; }.history-list button:hover,.history-list button.active { background: #eaf2ff; border-color: #d3e2fb; }.history-list button.active .history-icon { color: #fff; background: #2563eb; }.history-icon { display: grid; width: 25px; height: 25px; place-items: center; color: #4d79a9; background: #e6f0f8; border-radius: 4px; }.history-copy { min-width: 0; }.history-copy strong,.history-copy small,.history-copy em { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.history-copy strong { color: #29475d; font-size: 11px; }.history-copy small { margin-top: 3px; color: #778b99; font-size: 9px; }.history-copy em { margin-top: 5px; color: #9aabb6; font-size: 8px; font-style: normal; }.archive-button { display: flex; margin-top: auto; padding: 12px 4px 2px; align-items: center; justify-content: space-between; color: #667e8e; background: transparent; border: 0; border-top: 1px solid var(--line); cursor: pointer; font-size: 10px; }
.chat-workspace { display: flex; min-width: 0; min-height: 0; overflow: hidden; flex-direction: column; background: #fff; }.chat-toolbar { min-height: 58px; flex-wrap: wrap; justify-content: space-between; gap: 8px 12px; padding: 8px 16px; border-bottom: 1px solid var(--line); }.mode-switch { display: flex; flex: none; padding: 3px; background: #eef4f8; border-radius: 5px; }.mode-switch button { height: 30px; padding: 0 10px; color: #688090; background: transparent; border: 0; border-radius: 4px; cursor: pointer; font-size: 11px; }.mode-switch button.active { color: #2059be; background: #fff; box-shadow: 0 1px 4px rgb(48 78 100 / 12%); font-weight: 700; }.toolbar-selectors { min-width: 0; align-items: end; justify-content: flex-end; gap: 8px; }.selector-field { display: grid; min-width: 0; gap: 3px; }.selector-field > span { color: #7f919f; font-size: 11px; line-height: 16px; }.actor-field { width: 165px; }.knowledge-field { width: 150px; }.resource-field { width: 180px; }.actor-select,.knowledge-select,.resource-select { width: 100%; }.conversation-summary { gap: 10px; padding: 12px 18px; background: #f5f9ff; border-bottom: 1px solid #dfebf6; }.summary-avatar { display: grid; width: 35px; height: 35px; flex: none; place-items: center; color: #fff; background: #0891b2; border-radius: 5px; }.summary-avatar.single { background: #2563eb; }.conversation-summary div:nth-child(2) { min-width: 0; }.conversation-summary strong { font-size: 12px; }.conversation-summary p { margin: 3px 0 0; overflow: hidden; color: var(--muted); font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }.conversation-summary > span { margin-left: auto; color: #0f8064; font-size: 9px; white-space: nowrap; }
.message-stream { flex: 1 1 0; min-height: 0; padding: 12px 5%; overflow-y: auto; }.date-divider { display: flex; align-items: center; justify-content: center; margin: 3px 0 16px; color: #94a5b0; font-size: 9px; }.date-divider::before,.date-divider::after { width: 60px; height: 1px; margin: 0 8px; content: ''; background: #e7eef3; }.message-row { display: flex; gap: 9px; margin-bottom: 17px; }.message-row.user { flex-direction: row-reverse; }.message-avatar { display: grid; width: 30px; height: 30px; flex: none; place-items: center; color: #fff; background: #0891b2; border-radius: 5px; font-size: 10px; font-weight: 700; }.message-row.user .message-avatar { background: #49697d; }.message-content { max-width: min(700px, 85%); }.message-row.user .message-content { display: flex; align-items: flex-end; flex-direction: column; }.message-meta { gap: 7px; margin-bottom: 5px; }.message-meta strong { font-size: 10px; }.message-meta span { color: #98a7b1; font-size: 8px; }.message-bubble { padding: 10px 12px; color: #344f62; background: #f3f7fa; border: 1px solid #e0e9ef; border-radius: 3px 7px 7px; font-size: 11px; line-height: 1.75; }.message-row.user .message-bubble { color: #fff; background: #2563eb; border-color: #2563eb; border-radius: 7px 3px 7px 7px; }.run-card { margin-top: 8px; overflow: hidden; background: #fff; border: 1px solid #d6e3eb; border-radius: 5px; }.run-card header { display: flex; padding: 9px 11px; align-items: center; justify-content: space-between; color: #31526a; background: #f5fafc; border-bottom: 1px solid #dfebf1; font-size: 10px; font-weight: 700; }.run-card header em { color: #168265; font-size: 8px; font-style: normal; }.run-step { display: grid; grid-template-columns: 20px 1fr auto; gap: 8px; padding: 8px 11px; align-items: center; border-bottom: 1px solid #edf2f5; }.run-step > i { display: grid; width: 17px; height: 17px; place-items: center; color: #fff; background: #16a47a; border-radius: 50%; font-size: 8px; }.run-step strong,.run-step small { display: block; }.run-step strong { color: #315064; font-size: 9px; }.run-step small { margin-top: 2px; color: #8698a4; font-size: 8px; }.run-step > span { color: #92a2ac; font-size: 8px; }.run-card footer { display: flex; gap: 7px; padding: 9px 11px; }.run-card footer button { height: 27px; color: #275fbc; background: #edf4ff; border: 1px solid #d2e2fb; border-radius: 4px; cursor: pointer; font-size: 9px; }.citations { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 6px; }.citations span { padding: 4px 7px; color: #537084; background: #f7fafc; border: 1px solid #e0e8ed; border-radius: 3px; font-size: 8px; }.thinking { display: flex; height: 34px; align-items: center; gap: 4px; padding: 0 12px; background: #f3f7fa; border-radius: 6px; }.thinking i { width: 5px; height: 5px; background: #0891b2; border-radius: 50%; animation: pulse 1s infinite alternate; }.thinking i:nth-child(2) { animation-delay: .2s; }.thinking i:nth-child(3) { animation-delay: .4s; }.thinking span { margin-left: 5px; color: #6d8493; font-size: 9px; }
.suggestion-row { display: flex; gap: 6px; padding: 7px 16px 0; overflow-x: auto; }.suggestion-row button { flex: none; padding: 5px 8px; color: #527085; background: #f7fafc; border: 1px solid #dce7ef; border-radius: 4px; cursor: pointer; font-size: 9px; }.composer-shell { margin: 8px 16px 14px; border: 1px solid #bdcfdd; border-radius: 6px; box-shadow: 0 3px 12px rgb(40 78 106 / 6%); }.composer-shell:focus-within { border-color: #5d8fe6; box-shadow: 0 0 0 2px rgb(37 99 235 / 9%); }.composer-shell textarea { width: calc(100% - 24px); min-height: 46px; padding: 10px 12px 3px; resize: none; color: var(--ink); background: transparent; border: 0; outline: 0; font: inherit; font-size: 11px; }.composer-tools { min-height: 34px; justify-content: space-between; padding: 0 7px 5px; }.composer-tools > div { gap: 4px; }.composer-tools button { display: grid; width: 27px; height: 27px; place-items: center; color: #708797; background: transparent; border: 0; border-radius: 4px; cursor: pointer; }.composer-tools span { margin-left: 5px; color: #9aa9b3; font-size: 8px; }.composer-tools .send-button { color: #fff; background: #2563eb; }.composer-tools .send-button:disabled { background: #a9bdd9; cursor: not-allowed; }
.context-panel section { padding: 3px 0 14px; margin-bottom: 14px; border-bottom: 1px solid var(--line); }.context-panel section:last-child { border-bottom: 0; }.context-panel section > header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; color: #526d80; font-size: 10px; font-weight: 700; }.context-panel header b { padding: 2px 5px; color: #2563eb; background: #e7f0ff; border-radius: 3px; font-size: 8px; }.context-panel dl { margin: 0; }.context-panel dl div { display: grid; grid-template-columns: 65px 1fr; margin-bottom: 7px; font-size: 9px; }.context-panel dt { color: #8b9ca7; }.context-panel dd { margin: 0; color: #405e72; text-align: right; }.member-list { display: grid; gap: 9px; }.member-list > div { display: grid; grid-template-columns: 28px 1fr 7px; gap: 7px; align-items: center; }.member-list > div > span { display: grid; width: 28px; height: 28px; place-items: center; color: #fff; border-radius: 4px; font-size: 9px; }.member-list strong,.member-list small { display: block; }.member-list strong { color: #3e5a6d; font-size: 9px; }.member-list small { margin-top: 2px; color: #8a9ba6; font-size: 8px; }.member-list > div > i { width: 6px; height: 6px; background: #16a47a; border-radius: 50%; }.kb-list,.resource-list { display: grid; gap: 7px; }.kb-list > div { display: flex; gap: 7px; padding: 7px; color: #2563eb; background: #f2f7ff; border: 1px solid #dce8f8; border-radius: 4px; }.kb-list strong,.kb-list small,.resource-list strong,.resource-list small { display: block; }.kb-list strong,.resource-list strong { color: #405e72; font-size: 9px; }.kb-list small,.resource-list small { margin-top: 2px; color: #8a9ba6; font-size: 8px; }.resource-list > div { display: grid; grid-template-columns: 32px minmax(0, 1fr); gap: 7px; align-items: center; padding: 7px; background: #f0faf9; border: 1px solid #d5ece8; border-radius: 4px; }.resource-list > div > span { display: grid; width: 32px; height: 32px; place-items: center; color: #087b73; background: #dff4f1; border-radius: 4px; font-size: 9px; font-weight: 700; }.resource-list > div > div { min-width: 0; }.resource-list strong,.resource-list small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.manage-context { width: 100%; margin-top: 7px; padding: 6px; color: #547084; background: #fff; border: 1px dashed #cbd9e2; border-radius: 4px; cursor: pointer; font-size: 9px; }.safety-section p { margin: 7px 0; color: #567082; font-size: 9px; }.safety-section p i { display: inline-block; width: 6px; height: 6px; margin-right: 5px; background: #16a47a; border-radius: 50%; }.safety-section p i.warning { background: #f2b84b; }.safety-section small { color: #94a3ad; font-size: 8px; }.mobile-tabs { display: none; }
/* Readable typography scale for sustained operational use. */
.conversation-page { font-size: 14px; line-height: 22px; }
.conversation-brand strong { font-size: 16px; line-height: 24px; }
.conversation-brand small,.runtime-state { font-size: 12px; line-height: 18px; }
.quiet-button,.new-chat-button,.archive-button,.mode-switch button { font-size: 14px; }
.search-box { height: 38px; }.search-box input { font-size: 14px; }
.history-heading { font-size: 12px; line-height: 18px; }
.history-copy strong { font-size: 14px; line-height: 22px; }
.history-copy small,.history-copy em { font-size: 12px; line-height: 18px; }
.history-list button { padding-block: 10px; }
.chat-toolbar { min-height: 64px; }
.mode-switch button { height: 36px; }
.conversation-summary strong { font-size: 16px; line-height: 24px; }
.conversation-summary p,.conversation-summary > span { font-size: 12px; line-height: 18px; }
.date-divider,.message-meta span { font-size: 12px; line-height: 18px; }
.message-avatar { width: 34px; height: 34px; font-size: 12px; }
.message-meta strong,.message-bubble { font-size: 14px; line-height: 22px; }
.run-card header,.run-step strong { font-size: 14px; line-height: 22px; }
.run-card header em,.run-step small,.run-step > span,.citations span { font-size: 12px; line-height: 18px; }
.run-step { min-height: 52px; }.run-step > i { width: 20px; height: 20px; font-size: 12px; }
.run-card footer button,.suggestion-row button { min-height: 34px; font-size: 12px; }
.thinking span,.composer-tools span { font-size: 12px; line-height: 18px; }
.composer-shell textarea { min-height: 58px; font-size: 14px; line-height: 22px; }
.context-panel section > header { font-size: 14px; line-height: 22px; }
.context-panel header b { font-size: 12px; }
.context-panel dl div { grid-template-columns: 76px 1fr; font-size: 12px; line-height: 18px; }
.member-list > div { grid-template-columns: 32px 1fr 7px; }.member-list > div > span { width: 32px; height: 32px; font-size: 12px; }
.member-list strong,.kb-list strong,.resource-list strong { font-size: 14px; line-height: 22px; }
.member-list small,.kb-list small,.resource-list small,.safety-section small { font-size: 12px; line-height: 18px; }
.manage-context,.safety-section p { font-size: 12px; line-height: 18px; }
@keyframes pulse { to { opacity: .25; transform: translateY(-2px); } }
@media (prefers-reduced-motion: reduce) { .thinking i { animation: none; } }
@media (max-width: 1250px) { .conversation-layout { grid-template-columns: 230px minmax(420px, 1fr) 250px; }.toolbar-selectors { width: 100%; }.selector-field { flex: 1; width: auto; } }
@media (max-width: 900px) { .conversation-page { min-height: calc(100vh - 84px); min-height: calc(100dvh - 84px); }.conversation-topbar { height: 56px; padding: 0 12px; }.runtime-state { display: none; }.mobile-tabs { display: grid; grid-template-columns: repeat(3, 1fr); height: 38px; padding: 3px; background: #edf3f7; }.mobile-tabs button { color: #688090; background: transparent; border: 0; border-radius: 4px; }.mobile-tabs button.active { color: #2059be; background: #fff; font-weight: 700; }.conversation-layout,.focus-mode .conversation-layout { display: block; height: calc(100vh - 178px); height: calc(100dvh - 178px); min-height: 430px; }.focus-mode .conversation-layout { height: calc(100vh - 94px); height: calc(100dvh - 94px); }.history-panel,.chat-workspace,.context-panel { display: none; height: 100%; box-sizing: border-box; border: 0; }.history-panel.mobile-active,.chat-workspace.mobile-active,.context-panel.mobile-active { display: flex; }.context-panel.mobile-active { display: block; }.history-panel { max-width: none; }.toolbar-selectors { flex: 1; }.conversation-layout .chat-workspace { min-height: 0; }.message-stream { min-height: 120px; } }
@media (min-width: 761px) and (max-width: 900px) { .conversation-page { min-height: calc(100vh - 95px); min-height: calc(100dvh - 95px); }.conversation-layout { height: calc(100vh - 189px); height: calc(100dvh - 189px); } }
@media (max-width: 580px) { .conversation-brand small,.quiet-button span { display: none; }.conversation-brand strong { font-size: 16px; }.conversation-topbar .quiet-button { width: 34px; padding: 0; font-size: 0; }.conversation-topbar .quiet-button :deep(svg) { width: 14px; height: 14px; }.chat-toolbar { align-items: stretch; flex-direction: column; gap: 7px; }.mode-switch { display: grid; grid-template-columns: 1fr 1fr; }.toolbar-selectors { display: grid; grid-template-columns: 1fr 1fr; }.selector-field { width: 100%; }.resource-field { grid-column: 1 / -1; }.conversation-summary { padding: 9px 12px; }.conversation-summary > span { display: none; }.message-stream { padding: 10px 12px; }.message-content { max-width: calc(100% - 43px); }.run-card footer { flex-direction: column; }.suggestion-row { padding-left: 10px; }.composer-shell { margin: 7px 10px 10px; }.composer-tools span { display: none; } }
@media (max-width: 360px) { .conversation-topbar { padding-inline: 8px; }.brand-pulse { display: none; }.toolbar-selectors { grid-template-columns: 1fr; }.resource-field { grid-column: auto; }.conversation-summary p { white-space: normal; }.suggestion-row { max-width: 100%; }.run-card header { align-items: flex-start; flex-direction: column; gap: 4px; }.run-step { grid-template-columns: 18px minmax(0, 1fr); }.run-step > span { display: none; } }
</style>
