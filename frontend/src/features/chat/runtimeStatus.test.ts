import { describe, expect, it } from 'vitest';
import { runtimeStatusLabel } from './runtimeStatus';

describe('runtimeStatusLabel', () => {
  it('does not claim isolation while a run is only queued', () => {
    expect(runtimeStatusLabel('queued')).toBe('等待沙箱执行服务');
  });

  it('maps all frozen run states to explicit labels', () => {
    expect(runtimeStatusLabel('starting')).toBe('正在创建隔离运行环境');
    expect(runtimeStatusLabel('running')).toBe('沙箱运行中');
    expect(runtimeStatusLabel('waiting_approval')).toBe('等待人工确认');
    expect(runtimeStatusLabel('succeeded')).toBe('运行完成');
    expect(runtimeStatusLabel('failed')).toBe('运行失败');
    expect(runtimeStatusLabel('cancelled')).toBe('已取消');
  });
});
