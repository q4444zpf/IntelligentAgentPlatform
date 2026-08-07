import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { request, setCsrfToken } from './client';

beforeEach(() => {
  vi.stubGlobal('window', {
    setTimeout: globalThis.setTimeout,
    clearTimeout: globalThis.clearTimeout,
  });
});

afterEach(() => {
  setCsrfToken('');
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

function capturedHeaders(init: RequestInit | undefined) {
  return new Headers(init?.headers);
}

describe('request headers', () => {
  it('builds development identity headers with unit and roles', async () => {
    vi.resetModules();
    vi.stubEnv('VITE_DEV_UNIT_ID', 'unit-1');
    vi.stubEnv('VITE_DEV_PROJECT_ID', 'project-1');
    vi.stubEnv('VITE_DEV_USER_ID', 'user-1');
    vi.stubEnv('VITE_DEV_USER_ROLES', 'user,unit_auditor');

    const { identityHeaders } = await import('./client');

    expect(identityHeaders).toEqual({
      'X-Unit-ID': 'unit-1', 'X-Project-ID': 'project-1', 'X-User-ID': 'user-1',
      'X-User-Roles': 'user,unit_auditor',
    });
  });

  it.each([
    'VITE_DEV_UNIT_ID',
    'VITE_DEV_PROJECT_ID',
    'VITE_DEV_USER_ID',
  ])('omits development identity when %s is missing', async (missing) => {
    vi.resetModules();
    vi.stubEnv('VITE_DEV_UNIT_ID', 'unit-1');
    vi.stubEnv('VITE_DEV_PROJECT_ID', 'project-1');
    vi.stubEnv('VITE_DEV_USER_ID', 'user-1');
    vi.stubEnv('VITE_DEV_USER_ROLES', 'user');
    vi.stubEnv(missing, '');

    const { identityHeaders } = await import('./client');

    expect(identityHeaders).toEqual({});
  });

  it('omits the roles header when development roles are missing', async () => {
    vi.resetModules();
    vi.stubEnv('VITE_DEV_UNIT_ID', 'unit-1');
    vi.stubEnv('VITE_DEV_PROJECT_ID', 'project-1');
    vi.stubEnv('VITE_DEV_USER_ID', 'user-1');
    vi.stubEnv('VITE_DEV_USER_ROLES', '');
    vi.stubEnv('VITE_DEV_USER_ROLE', '');

    const { identityHeaders } = await import('./client');

    expect(identityHeaders).toEqual({
      'X-Unit-ID': 'unit-1',
      'X-Project-ID': 'project-1',
      'X-User-ID': 'user-1',
    });
    expect(identityHeaders).not.toHaveProperty('X-User-Roles');
    expect(Object.values(identityHeaders)).not.toContain('undefined');
  });

  it('adds JSON content type for a string request body', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}'));

    await request('/models/deepseek/test', {
      method: 'POST',
      body: JSON.stringify({ base_url: 'https://api.deepseek.com/v1' }),
    });

    expect(capturedHeaders(fetchMock.mock.calls[0]?.[1]).get('Content-Type')).toBe('application/json');
  });

  it('adds the CSRF token to state-changing requests', async () => {
    setCsrfToken('csrf-test-token');
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}'));

    await request('/identity/users', { method: 'POST', body: '{}' });

    expect(capturedHeaders(fetchMock.mock.calls[0]?.[1]).get('X-CSRF-Token')).toBe('csrf-test-token');
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
describe('request cancellation', () => {
  it('keeps the request timeout active when an external signal is supplied', async () => {
    vi.useFakeTimers();
    const external = new AbortController();
    vi.spyOn(globalThis, 'fetch').mockImplementation((_input, init) => (
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      })
    ));

    const pending = request('/slow', { signal: external.signal }, 20);
    await vi.advanceTimersByTimeAsync(20);

    await expect(pending).rejects.toMatchObject({ status: 408 });
    expect(external.signal.aborted).toBe(false);
    vi.useRealTimers();
  });

  it('preserves an external abort so callers can ignore cancellation', async () => {
    const external = new AbortController();
    vi.spyOn(globalThis, 'fetch').mockImplementation((_input, init) => (
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      })
    ));

    const pending = request('/cancelled', { signal: external.signal });
    external.abort();

    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  });
});
