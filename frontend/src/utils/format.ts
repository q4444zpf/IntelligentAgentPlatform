export const statusColor: Record<string, string> = {
  draft: 'default',
  reviewing: 'gold',
  published: 'green',
  disabled: 'red',
  queued: 'default',
  running: 'processing',
  success: 'success',
  failed: 'error',
  approved: 'green',
  issued: 'blue',
  normal: 'success',
  warning: 'warning',
  danger: 'error',
  low: 'green',
  medium: 'orange',
  high: 'red',
};

export const riskText: Record<string, string> = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
};

export const planStatusText: Record<string, string> = {
  draft: '草稿',
  running: '计算中',
  approved: '已审核',
  issued: '已下发',
};

export const stationStatusText: Record<string, string> = {
  normal: '正常',
  warning: '预警',
  danger: '超警',
};

export const runStatusText: Record<string, string> = {
  queued: '排队中',
  running: '运行中',
  success: '成功',
  failed: '失败',
};

export const stationTypeText: Record<string, string> = {
  rainfall: '雨量站',
  reservoir: '水库站',
  river: '河道站',
  gate: '闸站',
  pump: '泵站',
};

export function getText(map: Record<string, string>, key: unknown, fallback = '-') {
  return map[String(key)] || fallback;
}
