import { describe, expect, it } from 'vitest';
import { isRunActive, runtimeStatusLabel, teamRunEventLabel } from './runtimeStatus';

describe('runtimeStatusLabel', () => {
  it('does not claim isolation while a run is only queued', () => {
    expect(runtimeStatusLabel('queued')).toBe('等待沙箱执行服务');
  });

  it('maps all frozen run states to explicit labels', () => {
    expect(runtimeStatusLabel('starting')).toBe('正在创建隔离运行环境');
    expect(runtimeStatusLabel('running')).toBe('沙箱运行中');
    expect(runtimeStatusLabel('waiting_approval')).toBe('等待人工确认');
    expect(runtimeStatusLabel('succeeded')).toBe('运行完成');
    expect(runtimeStatusLabel('completed')).toBe('运行完成');
    expect(runtimeStatusLabel('failed')).toBe('运行失败');
    expect(runtimeStatusLabel('cancelled')).toBe('已取消');
  });

  it('marks only non-terminal run states as active', () => {
    expect(isRunActive('queued')).toBe(true);
    expect(isRunActive('running')).toBe(true);
    expect(isRunActive('completed')).toBe(false);
    expect(isRunActive('failed')).toBe(false);
    expect(isRunActive('cancelled')).toBe(false);
  });});

describe('teamRunEventLabel', () => {
  it('formats bounded Team progress without exposing arbitrary payload fields', () => {
    expect(teamRunEventLabel({ sequence: 1, event_type: 'team.task.started', payload: { agent_id: 'forecast', task_id: 't1', prompt: 'secret' } })).toBe('forecast · 任务 t1 执行中');
    expect(teamRunEventLabel({ sequence: 2, event_type: 'team.task.failed', payload: { agent_id: 'review', task_id: 't2' } })).toBe('review · 任务 t2 失败');
    expect(teamRunEventLabel({ sequence: 3, event_type: 'team.synthesis.completed', payload: { partial: true } })).toBe('团队汇总已完成（部分完成）');
  });
});
