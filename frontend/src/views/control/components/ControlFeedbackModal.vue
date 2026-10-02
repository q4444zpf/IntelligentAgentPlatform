<template>
  <a-modal :open="open" title="记录控制反馈" ok-text="提交反馈" :confirm-loading="saving" @ok="submit" @cancel="$emit('close')">
    <a-alert v-if="error || validationError" type="error" :message="error || validationError" show-icon class="modal-alert" />
    <a-form layout="vertical">
      <a-form-item label="实际状态（JSON）" required><a-textarea v-model:value="actualState" :rows="5" /></a-form-item>
      <a-form-item label="观测时间"><a-input v-model:value="observedAt" type="datetime-local" /></a-form-item>
    </a-form>
  </a-modal>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue';
import type { ControlFeedback } from '@/api/control';
const props = defineProps<{ open: boolean; saving: boolean; error: string }>();
const emit = defineEmits<{ close: []; submit: [value: ControlFeedback] }>();
const actualState = ref('{}'); const observedAt = ref(''); const validationError = ref('');
watch(() => props.open, (open) => { if (open) { actualState.value = '{}'; observedAt.value = ''; validationError.value = ''; } });
function submit() {
  try {
    const value: unknown = JSON.parse(actualState.value);
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error();
    validationError.value = '';
    emit('submit', { actual_state: value as Record<string, unknown>, ...(observedAt.value ? { observed_at: new Date(observedAt.value).toISOString() } : {}) });
  } catch { validationError.value = 'JSON 必须为对象'; }
}
</script>
<style scoped>.modal-alert{margin-bottom:16px}</style>
