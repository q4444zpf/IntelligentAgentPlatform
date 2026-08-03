import { describe, expect, it, vi } from 'vitest';
import { getRunEvents, parseSseFrame } from './runEvents';

describe('parseSseFrame', () => {
  it('parses persisted run events', () => {
    expect(parseSseFrame('id: 4\nevent: run.status\ndata: {"status":"running"}')).toEqual({
      sequence: 4,
      event_type: 'run.status',
      payload: { status: 'running' },
    });
  });
});

describe('getRunEvents', () => {
  it('passes an AbortSignal to the event-stream request', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(''));
    const controller = new AbortController();

    await getRunEvents('run/1', 0, controller.signal);

    expect(fetchMock.mock.calls[0]?.[1]?.signal).toBe(controller.signal);
  });
});
