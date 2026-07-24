import { request } from './client';

export interface SkillInfo {
  name: string;
  description: string;
  version: string;
  content: string;
  source: 'created' | 'imported';
  enabled: boolean;
  tags: string[];
  metadata: Record<string, unknown>;
  file_count: number;
  updated_at: string;
}

export interface SkillInput {
  description: string;
  content: string;
  tags: string[];
  enabled: boolean;
}

export interface SkillImportResult {
  imported: string[];
  skipped: string[];
  count: number;
  skills: SkillInfo[];
}

const json = (body: unknown): RequestInit => ({
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const skillsApi = {
  list: (signal?: AbortSignal) => request<SkillInfo[]>('/skills', { signal }),
  create: (body: SkillInput & { name: string }) => request<SkillInfo>('/skills', { method: 'POST', ...json(body) }),
  update: (name: string, body: SkillInput) => request<SkillInfo>(`/skills/${encodeURIComponent(name)}`, { method: 'PUT', ...json(body) }),
  toggle: (name: string) => request<SkillInfo>(`/skills/${encodeURIComponent(name)}/toggle`, { method: 'PATCH' }),
  remove: (name: string) => request<{ deleted: boolean }>(`/skills/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  importZip: (file: File, strategy: 'rename' | 'overwrite' | 'skip') => {
    const form = new FormData();
    form.append('file', file);
    return request<SkillImportResult>(`/skills/import?conflict_strategy=${strategy}`, { method: 'POST', body: form }, 30000);
  },
};
