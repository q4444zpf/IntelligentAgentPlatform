import { describe, expect, it } from 'vitest';
import { parseSseFrame } from './runEvents';

describe('parseSseFrame', () => {
  it('parses persisted run events', () => {
    expect(parseSseFrame('id: 4\nevent: run.status\ndata: {"status":"running"}')).toEqual({
      sequence: 4,
      event_type: 'run.status',
      payload: { status: 'running' },
    });
  });
});
