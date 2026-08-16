import { request } from './client';

export interface ArtifactInfo {
  id: string;
  filename: string;
  content_type: string;
  run_id?: string | null;
  size_bytes: number;
  created_at: string;
  status?: string;
}

export interface ArtifactDownloadInfo {
  url: string;
  expires_in?: number;
}

export const artifactsApi = {
  list: (signal?: AbortSignal) =>
    request<ArtifactInfo[]>('/artifacts', { signal }),
  download: (artifactId: string, signal?: AbortSignal) =>
    request<ArtifactDownloadInfo>(
      `/artifacts/${encodeURIComponent(artifactId)}/download`,
      { signal },
    ),
};
