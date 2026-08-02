export const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || '/api';
export const identityHeaders: Record<string, string> =
  import.meta.env.VITE_DEV_USER_ID && import.meta.env.VITE_DEV_PROJECT_ID
    ? {
        'X-User-ID': import.meta.env.VITE_DEV_USER_ID,
        'X-Project-ID': import.meta.env.VITE_DEV_PROJECT_ID,
        ...(import.meta.env.VITE_DEV_USER_ROLE
          ? { 'X-User-Role': import.meta.env.VITE_DEV_USER_ROLE }
          : {}),
      }
    : {};

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = 'ApiError';
  }
}

export async function request<T>(path: string, init: RequestInit = {}, timeoutMs = 8000): Promise<T> {
  const controller = new AbortController();
  const externalSignal = init.signal;
  let timedOut = false;
  const abortFromExternal = () => controller.abort(externalSignal?.reason);
  if (externalSignal?.aborted) abortFromExternal();
  else externalSignal?.addEventListener('abort', abortFromExternal, { once: true });

  const timeout = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const headers = new Headers({ Accept: 'application/json', ...identityHeaders, ...init.headers });
  if (typeof init.body === 'string' && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  try {
    const response = await fetch(`${apiBaseUrl}${path}`, {
      ...init,
      signal: controller.signal,
      headers,
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new ApiError(payload.detail || `请求失败（HTTP ${response.status}）`, response.status);
    }
    return (await response.json()) as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === 'AbortError') {
      if (!timedOut) throw error;
      throw new ApiError('请求超时，请检查后端服务状态', 408);
    }
    throw new ApiError('无法连接后端服务，请确认 API 已启动', 0);
  } finally {
    window.clearTimeout(timeout);
    externalSignal?.removeEventListener('abort', abortFromExternal);
  }
}
