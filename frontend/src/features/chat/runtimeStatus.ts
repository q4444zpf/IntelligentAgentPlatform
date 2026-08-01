const labels: Record<string, string> = {
  queued: '等待沙箱执行服务',
  starting: '正在创建隔离运行环境',
  running: '沙箱运行中',
  waiting_approval: '等待人工确认',
  succeeded: '运行完成',
  failed: '运行失败',
  cancelled: '已取消',
};

export function runtimeStatusLabel(status?: string): string {
  if (!status) return '尚未启动运行';
  return labels[status] ?? `运行状态：${status}`;
}
