export type ResourceType = 'agent' | 'mcp' | 'skill' | 'workflow';
export type PublishStatus = 'private_active' | 'pending_review' | 'public_active' | 'rejected';

export interface PlatformResource {
  id: string;
  name: string;
  type: ResourceType;
  owner: string;
  space: 'personal' | 'public' | 'system';
  status: PublishStatus;
  description: string;
  calls: number;
  updatedAt: string;
}

export interface ModelProvider {
  id: string;
  name: string;
  region: 'domestic' | 'global' | 'private' | 'custom';
  protocol: 'openai-compatible' | 'native' | 'custom-http';
  modelTypes: string[];
  endpoint: string;
  status: 'enabled' | 'disabled';
}

export interface SandboxTask {
  id: string;
  taskName: string;
  source: string;
  type: 'mcp' | 'skill' | 'file' | 'script';
  isolation: string;
  status: 'running' | 'success' | 'blocked' | 'failed';
  duration: string;
  risk: 'low' | 'medium' | 'high';
}
