<template>
  <a-modal :open="open" title="新建控制申请" width="min(680px, 100vw)" ok-text="提交申请" :confirm-loading="saving" @ok="submit" @cancel="$emit('close')">
    <a-alert v-if="error || validationError" type="error" :message="error || validationError" show-icon class="modal-alert" />
    <a-form layout="vertical">
      <a-form-item label="控制策略" required><a-select v-model:value="policyId" :options="policies.filter(item => item.enabled).map(item => ({ value: item.id, label: `${item.asset_type} · ${item.command_type}` }))" /></a-form-item>
      <a-form-item label="目标（JSON）" required><a-textarea v-model:value="target" :rows="2" /></a-form-item>
      <a-form-item label="参数（JSON）" required><a-textarea v-model:value="parameters" :rows="2" /></a-form-item>
      <a-form-item label="仿真工件 ID" required><a-input v-model:value="simulationArtifactId" /></a-form-item>
      <div class="form-pair"><a-form-item label="生效时间" required><a-input v-model:value="validFrom" type="datetime-local" /></a-form-item><a-form-item label="失效时间" required><a-input v-model:value="validUntil" type="datetime-local" /></a-form-item></div>
      <a-form-item label="预期状态（JSON）"><a-textarea v-model:value="expectedState" :rows="2" /></a-form-item>
      <a-form-item label="回滚计划（JSON）"><a-textarea v-model:value="rollbackPlan" :rows="2" /></a-form-item>
    </a-form>
  </a-modal>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue';
import type { ControlPolicy, ControlProposal, JsonObject } from '@/api/control';
const props = defineProps<{ open: boolean; policies: ControlPolicy[]; saving: boolean; error: string }>();
const emit = defineEmits<{ close: []; submit: [value: ControlProposal] }>();
const policyId = ref(''); const target = ref('{}'); const parameters = ref('{}'); const simulationArtifactId = ref('');
const validFrom = ref(''); const validUntil = ref(''); const expectedState = ref('{}'); const rollbackPlan = ref('{}'); const validationError = ref('');
watch(() => props.open, (open) => { if (open) { policyId.value = ''; target.value = '{}'; parameters.value = '{}'; simulationArtifactId.value = ''; validFrom.value = ''; validUntil.value = ''; expectedState.value = '{}'; rollbackPlan.value = '{}'; validationError.value = ''; } });
function object(text: string): JsonObject { const value: unknown = JSON.parse(text); if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('JSON 必须为对象'); return value as JsonObject; }
function submit() {
  try {
    if (!policyId.value || !simulationArtifactId.value.trim() || !validFrom.value || !validUntil.value) throw new Error('请填写必填项');
    if (new Date(validUntil.value) <= new Date(validFrom.value)) throw new Error('失效时间必须晚于生效时间');
    validationError.value = '';
    emit('submit', { policy_id: policyId.value, target: object(target.value), parameters: object(parameters.value), simulation_artifact_id: simulationArtifactId.value.trim(), valid_from: new Date(validFrom.value).toISOString(), valid_until: new Date(validUntil.value).toISOString(), expected_state: object(expectedState.value), rollback_plan: object(rollbackPlan.value), idempotency_key: crypto.randomUUID() });
  } catch (error) { validationError.value = error instanceof Error ? error.message : '表单无效'; }
}
</script>
<style scoped>.modal-alert{margin-bottom:16px}.form-pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}@media(max-width:600px){.form-pair{grid-template-columns:1fr}}</style>
