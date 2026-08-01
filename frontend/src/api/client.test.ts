import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { request } from './client';

beforeEach(() => {
  vi.stubGlobal('window', {
    setTimeout: globalThis.setTimeout,
    clearTimeout: globalThis.clearTimeout,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function capturedHeaders(init: RequestInit | undefined) {
  return new Headers(init?.headers);
}

describe('request headers', () => {
  it('adds JSON content type for a string request body', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}'));

    await request('/models/deepseek/test', {
      method: 'POST',
      body: JSON.stringify({ base_url: 'https://api.deepseek.com/v1' }),
    });

    expect(capturedHeaders(fetchMock.mock.calls[0]?.[1]).get('Content-Type')).toBe('application/json');
  });

  it('preserves an explicit content type', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}'));

    await request('/upload', {
      method: 'POST',
      body: 'payload',
      headers: { 'Content-Type': 'text/plain' },
    });

    expect(capturedHeaders(fetchMock.mock.calls[0]?.[1]).get('Content-Type')).toBe('text/plain');
  });

  it('does not add content type when there is no request body', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}'));

    await request('/models');

    expect(capturedHeaders(fetchMock.mock.calls[0]?.[1]).has('Content-Type')).toBe(false);
  });
});
