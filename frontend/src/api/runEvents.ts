import { ApiError, apiBaseUrl, identityHeaders } from './client';

export interface RunEvent {
  sequence: number;
  event_type: string;
  payload: Record<string, unknown>;
}

export function parseSseFrame(frame: string): RunEvent {
  const lines = Object.fromEntries(
    frame.split('\n').map((line) => {
      const index = line.indexOf(':');
      return [line.slice(0, index), line.slice(index + 1).trim()];
    }),
  );
  return {
    sequence: Number(lines.id),
    event_type: lines.event,
    payload: JSON.parse(lines.data),
  };
}

export async function getRunEvents(
  runId: string,
  afterSequence: number,
  signal?: AbortSignal,
): Promise<RunEvent[]> {
  const response = await fetch(`${apiBaseUrl}/agent-runs/${encodeURIComponent(runId)}/events`, {
    signal,
    headers: {
      Accept: 'text/event-stream',
      'Last-Event-ID': String(afterSequence),
      ...identityHeaders,
    },
  });
  if (!response.ok) {
    throw new ApiError(`运行事件读取失败（HTTP ${response.status}）`, response.status);
  }
  const text = await response.text();
  return text
    .split(/\r?\n\r?\n/)
    .filter((frame) => frame.trim())
    .map(parseSseFrame);
}
