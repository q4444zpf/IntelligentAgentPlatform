<template>
  <a-drawer :open="open" title="控制命令详情" width="min(720px, 100vw)" @close="$emit('close')">
    <a-spin :spinning="loading">
      <a-alert v-if="error" type="error" :message="error" show-icon />
      <template v-if="command">
        <div class="evidence-grid">
          <div><span>命令 ID</span><strong>{{ command.id }}</strong></div>
          <div><span>状态</span><strong>{{ command.status }}</strong></div>
          <div><span>审批进度</span><strong>{{ command.approval_count }} / {{ command.required_approvals }}</strong></div>
          <div><span>有效期</span><strong>{{ command.valid_from }} - {{ command.valid_until }}</strong></div>
          <div><span>仿真工件</span><strong>{{ command.simulation_artifact_id }}</strong></div>
          <div><span>仿真摘要</span><strong>{{ command.simulation_digest }}</strong></div>
          <div><span>策略摘要</span><strong>{{ command.policy_digest }}</strong></div>
          <div><span>参数摘要</span><strong>{{ command.parameter_digest }}</strong></div>
          <div><span>外部命令引用</span><strong>{{ command.external_command_ref || '无' }}</strong></div>
          <div><span>反馈观测时间</span><strong>{{ command.feedback_observed_at || '无' }}</strong></div>
          <div><span>最新尝试</span><strong>{{ latestAttempt ? `${latestAttempt.kind} · ${latestAttempt.outcome} · ${latestAttempt.id}` : '无' }}</strong></div>
        </div>
        <section><h3>仿真证明</h3><pre>{{ pretty(command.simulation_attestation) }}</pre></section>
        <section><h3>策略快照</h3><pre>{{ pretty({ policy: command.policy_snapshot, deviation_thresholds: command.deviation_thresholds_snapshot }) }}</pre></section>
        <section><h3>目标与参数</h3><pre>{{ pretty({ target: command.target, parameters: command.parameters }) }}</pre></section>
        <section><h3>回滚计划</h3><pre>{{ pretty(command.rollback_plan) }}</pre></section>
        <section><h3>预期与实际状态</h3><pre>{{ pretty({ expected: command.expected_state, actual: command.actual_state }) }}</pre></section>
        <section><h3>偏差结果</h3><pre>{{ pretty(command.deviation_result) }}</pre></section>
        <section><h3>审批记录</h3><div v-for="decision in command.approval_decisions" :key="decision.approver_id">{{ decision.approver_id }} · {{ decision.decision }} · {{ decision.decided_at }} · {{ decision.reason || '无备注' }}</div><a-empty v-if="!command.approval_decisions.length" description="暂无记录" /></section>
        <section><h3>执行与回滚尝试</h3><div v-for="attempt in command.attempts" :key="attempt.id">{{ attempt.id }} · {{ attempt.kind }} · {{ attempt.outcome }} · {{ attempt.external_id || '无外部引用' }} · {{ attempt.created_at }} · {{ attempt.completed_at || '进行中' }}</div><a-empty v-if="!command.attempts.length" description="暂无尝试" /></section>
        <section><h3>反馈记录</h3><div v-for="feedback in command.feedback_records" :key="feedback.id">{{ feedback.id }} · {{ feedback.recorder_id }} · {{ feedback.observed_at }}<pre>{{ pretty({ actual: feedback.actual_state, deviation: feedback.deviation_result }) }}</pre></div><a-empty v-if="!command.feedback_records.length" description="暂无反馈" /></section>
        <section><h3>回滚依据</h3><pre>{{ pretty(command.rollback_provenance) }}</pre></section>
      </template>
    </a-spin>
  </a-drawer>
</template>
<script setup lang="ts">
import { computed } from 'vue';
import type { ControlCommandDetail } from '@/api/control';
const props = defineProps<{ open: boolean; loading: boolean; command: ControlCommandDetail | null; error: string }>();
defineEmits<{ close: [] }>();
const pretty = (value: unknown) => JSON.stringify(value, null, 2) || '无';
const latestAttempt = computed(() => props.command?.attempts.at(-1));
</script>
<style scoped>
.evidence-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-bottom:20px}.evidence-grid>div{min-width:0;border-bottom:1px solid #edf0f2;padding-bottom:8px}.evidence-grid span{display:block;color:#68737b;font-size:12px}.evidence-grid strong{display:block;overflow-wrap:anywhere;font-weight:500}section{margin:18px 0}h3{font-size:14px;margin:0 0 8px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f8f8;padding:10px;border-radius:4px}@media(max-width:600px){.evidence-grid{grid-template-columns:1fr}}
</style>
