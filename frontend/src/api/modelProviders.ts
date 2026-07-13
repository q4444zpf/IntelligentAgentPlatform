export interface ApiModel {
  id: string;
  name: string;
  type: string;
  enabled: boolean;
  builtin: boolean;
  max_tokens: number;
  context_window: number;
  forward_reasoning: boolean;
  extra_config: Record<string, unknown>;
  supports_image?: boolean;
  supports_video?: boolean;
  supports_multimodal?: boolean;
  probe_source?: string;
}

export interface ApiProvider {
  id: string;
  name: string;
  kind: 'cloud' | 'local';
  base_url: string;
  masked_api_key: string;
  require_api_key: boolean;
  protocol: string;
  freeze_url: boolean;
  support_connection_check: boolean;
  support_model_discovery: boolean;
  api_key_prefixes: string[];
  generate_kwargs: Record<string, unknown>;
  custom_headers: Record<string, string>;
  auth_mode: 'api_key' | 'auth_token';
  configured: boolean;
  enabled: boolean;
  is_custom: boolean;
  is_free_tier: boolean;
  provider_group?: string;
  provider_variant?: string;
  models: ApiModel[];
}

const baseUrl = import.meta.env.VITE_API_BASE_URL || '/api';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `请求失败（HTTP ${response.status}）`);
  }
  return response.json() as Promise<T>;
}

export const modelProviderApi = {
  list: () => request<ApiProvider[]>('/models'),
  create: (body: { id: string; name: string; default_base_url: string; api_key_prefix: string; protocol: string }) =>
    request<ApiProvider>('/models/custom-providers', { method: 'POST', body: JSON.stringify(body) }),
  configure: (id: string, body: { name?: string; base_url: string; api_key?: string; protocol?: string; generate_kwargs?: Record<string, unknown>; custom_headers?: Record<string, string>; auth_mode?: 'api_key' | 'auth_token'; enabled?: boolean }) =>
    request<ApiProvider>(`/models/${encodeURIComponent(id)}/config`, { method: 'PUT', body: JSON.stringify(body) }),
  addModel: (id: string, body: { id: string; name?: string; type: string }) =>
    request<ApiProvider>(`/models/${encodeURIComponent(id)}/models`, { method: 'POST', body: JSON.stringify(body) }),
  configureModel: (providerId: string, modelId: string, body: { max_tokens: number; context_window: number; forward_reasoning: boolean; extra_config: Record<string, unknown>; enabled: boolean }) =>
    request<ApiProvider>(`/models/${encodeURIComponent(providerId)}/models/${encodeURIComponent(modelId)}/config`, { method: 'PUT', body: JSON.stringify(body) }),
  removeModel: (providerId: string, modelId: string) => request<ApiProvider>(`/models/${encodeURIComponent(providerId)}/models/${encodeURIComponent(modelId)}`, { method: 'DELETE' }),
  discoverModels: (providerId: string, save = true) => request<{ models: ApiModel[]; discovered_count: number; added_count: number }>(`/models/${encodeURIComponent(providerId)}/discover?save=${save}`, { method: 'POST' }),
  probeMultimodal: (providerId: string, modelId: string) => request<{ supports_image: boolean; supports_video: boolean; supports_multimodal: boolean; image_message: string; video_message: string }>(`/models/${encodeURIComponent(providerId)}/models/${encodeURIComponent(modelId)}/probe-multimodal`, { method: 'POST' }),
  testProvider: (id: string, body?: { base_url: string; api_key?: string; protocol?: string; generate_kwargs?: Record<string, unknown>; custom_headers?: Record<string, string>; auth_mode?: 'api_key' | 'auth_token' }) => request<{ success: boolean; message: string; latency_ms?: number }>(`/models/${encodeURIComponent(id)}/test`, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  testModel: (providerId: string, modelId: string) => request<{ success: boolean; message: string; latency_ms?: number }>(`/models/${encodeURIComponent(providerId)}/models/${encodeURIComponent(modelId)}/test`, { method: 'POST' }),
  getActive: () => request<{ provider_id: string; model: string }>('/models/active'),
  setActive: (provider_id: string, model: string) => request<{ provider_id: string; model: string }>('/models/active', { method: 'PUT', body: JSON.stringify({ provider_id, model }) }),
};
