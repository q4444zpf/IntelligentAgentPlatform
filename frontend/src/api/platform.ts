import { request } from './client';

export interface PlatformOverview {
  status: string;
  service: string;
  version: string;
  checked_at: string;
  provider_count: number;
  configured_provider_count: number;
  model_count: number;
  enabled_model_count: number;
  active_provider_id: string;
  active_model: string;
}

export const platformApi = {
  overview: (signal?: AbortSignal) => request<PlatformOverview>('/platform/overview', { signal }),
  health: (signal?: AbortSignal) => request<{ status: string; service: string; version: string }>('/health', { signal }),
};
