<template>
  <section class="control-policy-panel">
    <div class="panel-heading"><h2>控制策略</h2><a-space><a-tooltip title="刷新策略"><a-button aria-label="刷新策略" :loading="loading" @click="$emit('refresh')"><template #icon><ReloadOutlined /></template></a-button></a-tooltip><a-button v-if="canManage" type="primary" aria-label="新建策略" @click="openCreate"><template #icon><PlusOutlined /></template>新建策略</a-button></a-space></div>
    <a-alert v-if="error" type="error" :message="error" show-icon class="panel-alert" />
    <div v-if="policies.length" class="policy-list">
      <div v-for="policy in policies" :key="policy.id" class="policy-row">
        <div class="policy-main"><strong>{{ policy.asset_type }} · {{ policy.command_type }}</strong><span>{{ policy.id }} · 第 {{ policy.revision }} 版</span></div>
        <div class="policy-meta"><span>{{ policy.approval_mode === 'dual' ? '双人审批' : '单人审批' }}</span><span>{{ policy.max_command_lifetime_seconds }} 秒</span><a-tag :color="policy.enabled ? 'green' : 'default'">{{ policy.enabled ? '启用' : '停用' }}</a-tag></div>
        <a-button v-if="canManage" size="small" :aria-label="`编辑策略 ${policy.id}`" @click="openEdit(policy)">编辑</a-button>
      </div>
    </div>
    <a-empty v-else description="暂无控制策略" />
    <a-modal :open="editorOpen" :title="editing ? '编辑控制策略' : '新建控制策略'" ok-text="保存" :confirm-loading="saving" @ok="submit" @cancel="editorOpen = false">
      <a-alert v-if="validationError" type="error" :message="validationError" show-icon class="panel-alert" />
      <a-form layout="vertical">
        <div class="form-pair"><a-form-item label="资产类型" required><a-input v-model:value="form.asset_type" :disabled="Boolean(editing)" /></a-form-item><a-form-item label="命令类型" required><a-input v-model:value="form.command_type" :disabled="Boolean(editing)" /></a-form-item></div>
        <a-form-item label="参数 Schema（JSON）" required><a-textarea v-model:value="form.parameter_schema" :rows="3" /></a-form-item>
        <div class="form-pair"><a-form-item label="最大有效期（秒）" required><a-input-number v-model:value="form.max_command_lifetime_seconds" :min="1" :max="31536000" class="full-width" /></a-form-item><a-form-item label="审批模式"><a-select v-model:value="form.approval_mode" :options="[{ label: '单人审批', value: 'single' }, { label: '双人审批', value: 'dual' }]" /></a-form-item></div>
        <a-form-item label="偏差阈值（JSON）"><a-textarea v-model:value="form.deviation_thresholds" :rows="2" /></a-form-item>
        <a-form-item label="启用"><a-switch v-model:checked="form.enabled" /></a-form-item>
      </a-form>
    </a-modal>
  </section>
</template>
<script setup lang="ts">
import { reactive, ref } from 'vue';
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons-vue';
import type { ControlPolicy, ControlPolicyInput, JsonObject } from '@/api/control';
defineProps<{ policies: ControlPolicy[]; canManage: boolean; loading: boolean; saving: boolean; error: string }>();
const emit = defineEmits<{ refresh: []; save: [id: string | null, value: ControlPolicyInput] }>();
const empty = () => ({ asset_type: '', command_type: '', parameter_schema: '{}', max_command_lifetime_seconds: 600, approval_mode: 'single' as 'single' | 'dual', deviation_thresholds: '{}', enabled: true });
const form = reactive(empty()); const editorOpen = ref(false); const editing = ref<string | null>(null); const validationError = ref('');
function reset(value: ReturnType<typeof empty>) { Object.assign(form, value); validationError.value = ''; editorOpen.value = true; }
function openCreate() { editing.value = null; reset(empty()); }
function openEdit(policy: ControlPolicy) { editing.value = policy.id; reset({ asset_type: policy.asset_type, command_type: policy.command_type, parameter_schema: JSON.stringify(policy.parameter_schema, null, 2), max_command_lifetime_seconds: policy.max_command_lifetime_seconds, approval_mode: policy.approval_mode, deviation_thresholds: JSON.stringify(policy.deviation_thresholds, null, 2), enabled: policy.enabled }); }
function object(text: string): JsonObject { const value: unknown = JSON.parse(text); if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('JSON 必须为对象'); return value as JsonObject; }
function submit() {
  try {
    if (!form.asset_type.trim() || !form.command_type.trim()) throw new Error('请填写资产和命令类型');
    emit('save', editing.value, { asset_type: form.asset_type.trim(), command_type: form.command_type.trim(), parameter_schema: object(form.parameter_schema), max_command_lifetime_seconds: form.max_command_lifetime_seconds, approval_mode: form.approval_mode, deviation_thresholds: object(form.deviation_thresholds), enabled: form.enabled });
  } catch (error) { validationError.value = error instanceof Error ? error.message : '表单无效'; }
}
function closeEditor() { editorOpen.value = false; }
defineExpose({ closeEditor });
</script>
<style scoped>
.control-policy-panel{min-width:0}.panel-heading{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px}.panel-heading h2{font-size:17px;margin:0}.panel-alert{margin-bottom:12px}.policy-row{display:flex;align-items:center;gap:16px;padding:14px 0;border-top:1px solid #e8edec}.policy-main{flex:1;min-width:0}.policy-main strong,.policy-main span{display:block;overflow-wrap:anywhere}.policy-main span{color:#68737b;font-size:12px}.policy-meta{display:flex;align-items:center;gap:12px;color:#526068}.form-pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.full-width{width:100%}@media(max-width:600px){.policy-row{align-items:flex-start;flex-wrap:wrap}.policy-meta{width:100%;flex-wrap:wrap}.form-pair{grid-template-columns:1fr}}
</style>
