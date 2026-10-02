<template>
  <div class="control-page">
    <header class="page-heading"><div><h1>调度控制</h1><p>控制申请、审批、执行与反馈</p></div><a-space wrap><a-button v-if="canPropose" type="primary" aria-label="新建控制申请" @click="proposalOpen = true"><template #icon><PlusOutlined /></template>新建申请</a-button><a-tooltip title="刷新控制命令"><a-button aria-label="刷新控制命令" :loading="loading" @click="loadCommands"><template #icon><ReloadOutlined /></template></a-button></a-tooltip></a-space></header>
    <a-alert v-if="error" class="page-error" type="error" :message="error" show-icon />
    <div class="control-workflow">
      <section class="control-command-list">
        <div class="section-heading"><h2>控制命令</h2><div class="filters"><a-select v-model:value="statusFilter" aria-label="控制状态" :options="statusOptions" @change="loadCommands" /><a-select v-model:value="policyFilter" aria-label="控制策略筛选" :options="policyOptions" @change="loadCommands" /></div></div>
        <div class="column-head"><span>命令 / 目标</span><span>审批</span><span>有效期</span><span>状态</span><span>操作</span></div>
        <div v-if="commands.length" class="command-rows">
          <article v-for="command in commands" :key="command.id" class="command-row">
            <div class="command-main"><strong>{{ command.id }}</strong><span>{{ command.target.asset_type || command.policy_id }} · {{ command.target.id || command.target.asset_id || '目标' }}</span></div>
            <div class="command-meta"><small>审批</small><strong>{{ command.approval_count }} / {{ command.required_approvals }}</strong></div>
            <div class="command-meta validity"><small>有效期</small><span>{{ formatTime(command.valid_from) }}<br />{{ formatTime(command.valid_until) }}</span></div>
            <div class="command-meta"><small>状态</small><a-tag :color="statusColor(command.status)">{{ statusText(command.status) }}</a-tag></div>
            <div class="row-actions">
              <a-tooltip title="查看命令详情"><a-button aria-label="查看命令详情" :disabled="busyId === command.id" @click="openDetail(command)"><template #icon><FileSearchOutlined /></template></a-button></a-tooltip>
              <a-button v-if="canApprove && command.status === 'pending_approval'" size="small" :disabled="busyId === command.id" aria-label="批准命令" @click="runDecision(command, 'approve')">批准</a-button>
              <a-button v-if="canApprove && command.status === 'pending_approval'" size="small" danger :disabled="busyId === command.id" aria-label="拒绝命令" @click="runDecision(command, 'reject')">拒绝</a-button>
              <a-button v-if="canExecute && command.status === 'approved'" size="small" type="primary" :disabled="busyId === command.id" aria-label="执行命令" @click="requestConfirmation(command, 'execute')">执行</a-button>
              <a-button v-if="canExecute && command.recovery_operation" size="small" :danger="command.recovery_operation.kind === 'rollback'" :disabled="busyId === command.id" :aria-label="command.recovery_operation.kind === 'execute' ? '核对执行结果' : '核对回滚结果'" @click="requestConfirmation(command, command.recovery_operation.kind)">{{ command.recovery_operation.kind === 'execute' ? '核对执行结果' : '核对回滚结果' }}</a-button>
              <a-button v-if="canFeedback && feedbackStatuses.includes(command.status)" size="small" :disabled="busyId === command.id" aria-label="记录反馈" @click="openFeedback(command)">反馈</a-button>
              <a-button v-if="canExecute && rollbackStatuses.includes(command.status) && command.recovery_operation?.kind !== 'rollback'" size="small" danger :disabled="busyId === command.id" aria-label="回滚命令" @click="requestConfirmation(command, 'rollback')">回滚</a-button>
            </div>
          </article>
        </div>
        <a-empty v-else-if="!loading" description="暂无控制命令" />
        <a-spin v-if="loading && !commands.length" />
      </section>
      <ControlPolicyPanel ref="policyPanel" :policies="policies" :can-manage="canManage" :loading="policiesLoading" :saving="policySaving" :error="policyError" @refresh="loadPolicies" @save="savePolicy" />
    </div>
    <ControlCommandProposalModal :open="proposalOpen" :policies="policies" :saving="proposalSaving" :error="proposalError" @close="proposalOpen = false" @submit="submitProposal" />
    <ControlFeedbackModal :open="feedbackOpen" :saving="feedbackSaving" :error="feedbackError" @close="feedbackOpen = false" @submit="submitFeedback" />
    <ControlCommandDetailDrawer :open="detailOpen" :loading="detailLoading" :command="detail" :error="detailError" @close="detailOpen = false" />
    <a-modal :open="Boolean(confirmTarget)" :title="confirmKind === 'execute' ? '确认执行控制命令' : '确认物理回滚'" :ok-text="confirmKind === 'execute' ? '确认执行' : '确认物理回滚'" :ok-button-props="{ danger: confirmKind === 'rollback', 'aria-label': confirmKind === 'execute' ? '确认执行命令' : '确认物理回滚命令' }" :confirm-loading="Boolean(busyId)" @ok="confirmAction" @cancel="confirmTarget = null">
      <p>{{ confirmKind === 'execute' ? '即将向外部控制系统下发命令。' : '即将向外部控制系统执行物理回滚。' }}</p><strong>{{ confirmTarget?.id }}</strong>
    </a-modal>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { FileSearchOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons-vue';
import { ApiError } from '@/api/client';
import { controlApi, type ControlCommandDetail, type ControlCommandListItem, type ControlFeedback, type ControlPolicy, type ControlPolicyInput, type ControlProposalDraft, type ControlStatus } from '@/api/control';
import { usePermissionStore } from '@/stores/permission';
import { controlActionKeys, type ControlActionKind } from './controlActionKeys';
import { controlProposalKeys } from './controlProposalKeys';
import ControlPolicyPanel from './components/ControlPolicyPanel.vue';
import ControlCommandProposalModal from './components/ControlCommandProposalModal.vue';
import ControlCommandDetailDrawer from './components/ControlCommandDetailDrawer.vue';
import ControlFeedbackModal from './components/ControlFeedbackModal.vue';

const permission = usePermissionStore();
const canManage = computed(() => permission.hasPermission('control:manage'));
const canPropose = computed(() => permission.hasPermission('control:propose'));
const canApprove = computed(() => permission.hasPermission('control:approve'));
const canExecute = computed(() => permission.hasPermission('control:execute'));
const canFeedback = computed(() => permission.hasPermission('control:feedback'));
const commands = ref<ControlCommandListItem[]>([]); const policies = ref<ControlPolicy[]>([]);
const loading = ref(false); const policiesLoading = ref(false); const error = ref(''); const policyError = ref('');
const statusFilter = ref('all'); const policyFilter = ref('all');
const busyId = ref(''); const detailOpen = ref(false); const detailLoading = ref(false); const detailError = ref(''); const detail = ref<ControlCommandDetail | null>(null);
const proposalOpen = ref(false); const proposalSaving = ref(false); const proposalError = ref('');
const feedbackOpen = ref(false); const feedbackSaving = ref(false); const feedbackError = ref(''); const feedbackTarget = ref<ControlCommandListItem | null>(null);
const policySaving = ref(false); const policyPanel = ref<InstanceType<typeof ControlPolicyPanel> | null>(null);
const confirmTarget = ref<ControlCommandListItem | null>(null); const confirmKind = ref<ControlActionKind>('execute');
const feedbackStatuses: ControlStatus[] = ['awaiting_feedback', 'unknown', 'succeeded', 'deviation', 'rollback_pending'];
const rollbackStatuses: ControlStatus[] = ['failed', 'unknown', 'deviation', 'rollback_pending'];
const statusOptions = [{ value: 'all', label: '全部状态' }, ...(['pending_approval','approved','executing','awaiting_feedback','succeeded','failed','unknown','deviation','rollback_pending','rolled_back','rejected','expired'] as ControlStatus[]).map(value => ({ value, label: statusText(value) }))];
const policyOptions = computed(() => [{ value: 'all', label: '全部策略' }, ...policies.value.map(item => ({ value: item.id, label: `${item.asset_type} · ${item.command_type}` }))]);
function statusText(value: ControlStatus) { return ({ pending_approval: '待审批', approved: '已批准', rejected: '已拒绝', expired: '已过期', executing: '执行中', awaiting_feedback: '待反馈', succeeded: '已完成', failed: '失败', unknown: '状态未知', deviation: '偏差', rollback_pending: '待回滚', rolled_back: '已回滚' })[value]; }
function statusColor(value: ControlStatus) { return rollbackStatuses.includes(value) ? 'red' : value === 'succeeded' || value === 'rolled_back' ? 'green' : 'blue'; }
function formatTime(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN'); }
function safeError(cause: unknown) { return cause instanceof ApiError ? cause.message : '操作失败，请重试'; }
async function loadCommands() { loading.value = true; error.value = ''; try { commands.value = await controlApi.listCommands({ ...(statusFilter.value !== 'all' ? { status: statusFilter.value as ControlStatus } : {}), ...(policyFilter.value !== 'all' ? { policy_id: policyFilter.value } : {}) }); } catch (cause) { error.value = safeError(cause); } finally { loading.value = false; } }
async function loadPolicies() { policiesLoading.value = true; policyError.value = ''; try { policies.value = await controlApi.listPolicies(); } catch (cause) { policyError.value = safeError(cause); } finally { policiesLoading.value = false; } }
async function openDetail(command: ControlCommandListItem) { detailOpen.value = true; detailLoading.value = true; detailError.value = ''; detail.value = null; try { detail.value = await controlApi.getCommand(command.id); } catch (cause) { detailError.value = safeError(cause); } finally { detailLoading.value = false; } }
async function runDecision(command: ControlCommandListItem, kind: 'approve' | 'reject') { if (!canApprove.value || busyId.value) return; busyId.value = command.id; error.value = ''; try { if (kind === 'approve') await controlApi.approve(command.id); else await controlApi.reject(command.id); await loadCommands(); } catch (cause) { error.value = safeError(cause); } finally { busyId.value = ''; } }
function requestConfirmation(command: ControlCommandListItem, kind: ControlActionKind) { if (!canExecute.value || busyId.value) return; confirmTarget.value = command; confirmKind.value = kind; }
function terminalIdempotencyConflict(cause: unknown) { return cause instanceof ApiError && cause.status === 409 && cause.code === 'control_idempotency_conflict'; }
async function confirmAction() {
  const command = confirmTarget.value; const kind = confirmKind.value;
  if (!command || !canExecute.value || busyId.value) return;
  busyId.value = command.id; error.value = ''; confirmTarget.value = null;
  const key = command.recovery_operation?.kind === kind
    ? command.recovery_operation.request_key
    : controlActionKeys.keyFor(command.id, kind);
  try {
    if (kind === 'execute') await controlApi.execute(command.id, key); else await controlApi.rollback(command.id, key);
    controlActionKeys.clear(command.id, kind);
    await loadCommands();
  } catch (cause) {
    if (terminalIdempotencyConflict(cause)) controlActionKeys.clear(command.id, kind);
    error.value = safeError(cause);
  } finally { busyId.value = ''; }
}
function openFeedback(command: ControlCommandListItem) { if (!canFeedback.value || busyId.value) return; feedbackTarget.value = command; feedbackError.value = ''; feedbackOpen.value = true; }
async function submitFeedback(value: ControlFeedback) { const command = feedbackTarget.value; if (!command || !canFeedback.value) return; feedbackSaving.value = true; busyId.value = command.id; feedbackError.value = ''; try { await controlApi.feedback(command.id, value); feedbackOpen.value = false; await loadCommands(); } catch (cause) { feedbackError.value = safeError(cause); } finally { busyId.value = ''; feedbackSaving.value = false; } }
async function submitProposal(value: ControlProposalDraft) {
  if (!canPropose.value || proposalSaving.value) return;
  const material = { policy_id: value.policy_id, target: value.target, parameters: value.parameters, simulation_artifact_id: value.simulation_artifact_id, valid_from: value.valid_from, valid_until: value.valid_until, rollback_plan: value.rollback_plan, expected_state: value.expected_state, run_id: value.run_id, team_version_id: value.team_version_id };
  const scope = permission.authContext
    ? JSON.stringify([permission.authContext.unit_id, permission.authContext.current_project_id, permission.authContext.user.id])
    : JSON.stringify(['demo', permission.userName]);
  const signature = JSON.stringify(material, (_key, item: unknown) =>
    item && typeof item === 'object' && !Array.isArray(item)
      ? Object.fromEntries(Object.entries(item).sort(([left], [right]) => left.localeCompare(right)))
      : item);
  const key = controlProposalKeys.keyFor(scope, signature);
  proposalSaving.value = true; proposalError.value = '';
  try {
    await controlApi.propose({ ...material, idempotency_key: key });
    controlProposalKeys.clear(scope, signature);
    proposalOpen.value = false;
    await loadCommands();
  } catch (cause) {
    if (terminalIdempotencyConflict(cause)) controlProposalKeys.clear(scope, signature);
    proposalError.value = safeError(cause);
  } finally { proposalSaving.value = false; }
}
async function savePolicy(id: string | null, value: ControlPolicyInput) { if (!canManage.value) return; policySaving.value = true; policyError.value = ''; try { if (id) await controlApi.updatePolicy(id, { parameter_schema: value.parameter_schema, max_command_lifetime_seconds: value.max_command_lifetime_seconds, approval_mode: value.approval_mode, deviation_thresholds: value.deviation_thresholds, enabled: value.enabled }); else await controlApi.createPolicy(value); policyPanel.value?.closeEditor(); await loadPolicies(); } catch (cause) { policyError.value = safeError(cause); } finally { policySaving.value = false; } }
onMounted(() => { void loadCommands(); void loadPolicies(); });
</script>
<style scoped>
.control-page{min-width:0;color:#233139}.page-heading,.section-heading{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}.page-heading{padding:0 0 20px;border-bottom:1px solid #dce3e2}.page-heading h1{font-size:22px;margin:0}.page-heading p{color:#6b777d;margin:4px 0 0}.page-error{margin:16px 0}.control-workflow{display:grid;grid-template-columns:minmax(0,2fr) minmax(280px,1fr);gap:28px;margin-top:20px}.control-command-list,.control-policy-panel{min-width:0}.section-heading{margin-bottom:12px}.section-heading h2{font-size:17px;margin:0}.filters{display:flex;gap:8px;flex-wrap:wrap}.filters :deep(.ant-select){min-width:130px}.column-head,.command-row{display:grid;grid-template-columns:minmax(160px,1.35fr) 72px minmax(150px,1fr) 94px minmax(180px,1.3fr);gap:12px;align-items:center}.column-head{font-size:12px;color:#708087;padding:10px 12px;background:#f4f7f7}.command-row{padding:14px 12px;border-bottom:1px solid #e6ebea}.command-main{min-width:0}.command-main strong,.command-main span{display:block;overflow-wrap:anywhere}.command-main span{color:#617079;font-size:12px}.command-meta{min-width:0}.command-meta small{display:none}.command-meta span{font-size:12px}.row-actions{display:flex;align-items:center;gap:5px;flex-wrap:wrap}.validity{line-height:1.45}@media(max-width:1180px){.control-workflow{grid-template-columns:1fr}.column-head,.command-row{grid-template-columns:minmax(150px,1.3fr) 65px minmax(135px,1fr) 90px minmax(180px,1.3fr)}}@media(max-width:720px){.control-workflow{gap:24px}.column-head{display:none}.command-row{grid-template-columns:minmax(0,1fr) auto;gap:10px 12px;padding:14px 0}.command-main{grid-column:1 / -1}.command-meta small{display:block;color:#718087;font-size:11px}.row-actions{grid-column:1 / -1}.filters{width:100%}.filters :deep(.ant-select){flex:1;min-width:0}.validity{grid-column:1 / -1}}
</style>
