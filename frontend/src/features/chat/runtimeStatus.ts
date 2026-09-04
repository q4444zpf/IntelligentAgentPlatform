import type { RunEvent } from '@/api/runEvents';

const labels: Record<string, string> = {
  queued: '等待沙箱执行服务',
  starting: '正在创建隔离运行环境',
  running: '沙箱运行中',
  waiting_approval: '等待人工确认',
  succeeded: '运行完成',
  completed: '运行完成',
  failed: '运行失败',
  cancelled: '已取消',
};

export function runtimeStatusLabel(status?: string): string {
  if (!status) return '尚未启动运行';
  return labels[status] ?? `运行状态：${status}`;
}

const terminalStatuses = new Set(['completed', 'succeeded', 'failed', 'cancelled']);

export function isRunActive(status?: string): boolean {
  return Boolean(status && !terminalStatuses.has(status));
}

const teamEventTypes = new Set([
  'team.plan.created', 'team.task.started', 'team.task.completed', 'team.task.failed',
  'team.synthesis.started', 'team.synthesis.completed',
]);

export function isTeamRunEvent(event: RunEvent): boolean {
  return teamEventTypes.has(event.event_type);
}

export function teamRunEventLabel(event: RunEvent): string {
  const agent = typeof event.payload.member_name === 'string' ? event.payload.member_name
    : typeof event.payload.agent_id === 'string' ? event.payload.agent_id : '团队成员';
  const task = typeof event.payload.task_id === 'string' ? event.payload.task_id : '未命名任务';
  if (event.event_type === 'team.plan.created') return '团队执行计划已生成';
  if (event.event_type === 'team.task.started') return `${agent} · 任务 ${task} 执行中`;
  if (event.event_type === 'team.task.completed') return `${agent} · 任务 ${task} 已完成`;
  if (event.event_type === 'team.task.failed') return `${agent} · 任务 ${task} 失败`;
  if (event.event_type === 'team.synthesis.started') return '主管正在汇总团队结论';
  if (event.event_type === 'team.synthesis.completed') return `团队汇总已完成${event.payload.partial === true ? '（部分完成）' : ''}`;
  return '';
}
