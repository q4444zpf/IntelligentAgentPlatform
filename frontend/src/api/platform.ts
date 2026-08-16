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

export interface ServiceStatus {
  name: string;
  status: 'healthy' | 'unhealthy' | 'disabled';
  detail: string;
}

export interface PlatformServices {
  checked_at: string;
  services: ServiceStatus[];
}

export const platformApi = {
  overview: (signal?: AbortSignal) => request<PlatformOverview>('/platform/overview', { signal }),
  services: (signal?: AbortSignal) => request<PlatformServices>('/platform/services', { signal }),
  health: (signal?: AbortSignal) => request<{ status: string; service: string; version: string }>('/health', { signal }),
};
