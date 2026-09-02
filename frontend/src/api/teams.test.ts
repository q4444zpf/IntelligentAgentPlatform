import { beforeEach, describe, expect, it, vi } from 'vitest';

import { request } from './client';
import { teamsApi } from './teams';

vi.mock('./client', () => ({ request: vi.fn() }));

describe('teamsApi', () => {
  beforeEach(() => vi.clearAllMocks());

  it('uses the scoped Team catalogue and encodes resource ids', async () => {
    vi.mocked(request).mockResolvedValue([]);
    const signal = new AbortController().signal;
    await teamsApi.list({ enabled: true, published: true }, signal);
    expect(request).toHaveBeenCalledWith('/collaboration/teams?enabled=true&published=true', { signal });

    await teamsApi.getVersion('team/1', 2, signal);
    expect(request).toHaveBeenLastCalledWith('/collaboration/teams/team%2F1/versions/2', { signal });
  });

  it('saves a revision-bound draft and invokes lifecycle commands', async () => {
    vi.mocked(request).mockResolvedValue({});
    const draft = {
      supervisor: { agent_id: 'forecast', responsibility: '统筹研判', tool_ids: [], skill_names: [], knowledge_source_ids: [] },
      members: [{ agent_id: 'review', responsibility: '复核结论', tool_ids: [], skill_names: [], knowledge_source_ids: [] }],
      tool_ids: [], skill_names: [], knowledge_source_ids: [], max_steps: 8,
      max_parallel_members: 1, timeout_seconds: 600, failure_strategy: 'fail_fast' as const,
      approval_policy_id: null,
    };
    await teamsApi.saveDraft('team/1', 3, draft);
    expect(request).toHaveBeenCalledWith('/collaboration/teams/team%2F1/draft', {
      method: 'PUT', body: JSON.stringify({ revision: 3, draft }),
    });
    await teamsApi.publish('team/1');
    await teamsApi.setEnabled('team/1', false);
    expect(request).toHaveBeenNthCalledWith(2, '/collaboration/teams/team%2F1/publish', { method: 'POST' });
    expect(request).toHaveBeenNthCalledWith(3, '/collaboration/teams/team%2F1/disable', { method: 'POST' });
  });

  it('submits only user-editable Team fields without client identifiers or Agent snapshots', async () => {
    vi.mocked(request).mockResolvedValue({});
    const draft = {
      supervisor: {
        agent_id: 'forecast', responsibility: '统筹研判', tool_ids: ['forecast.read'], skill_names: ['forecast'], knowledge_source_ids: ['knowledge.rainfall'],
        agent_definition_digest: 'forged-digest', agent_definition: { system_prompt: 'forged' },
      },
      members: [{
        agent_id: 'review', responsibility: '复核结论', tool_ids: ['review.read'], skill_names: [], knowledge_source_ids: [],
        agent_definition_digest: 'forged-digest', agent_definition: { system_prompt: 'forged' },
      }],
      tool_ids: ['forecast.read', 'review.read'], skill_names: ['forecast'], knowledge_source_ids: ['knowledge.rainfall'],
      max_steps: 8, max_parallel_members: 1, timeout_seconds: 600, failure_strategy: 'fail_fast' as const,
      approval_policy_id: 'control-commands',
    };

    await teamsApi.create({ name: '联合研判', description: '防洪会商' });
    await teamsApi.saveDraft('team/1', 3, draft);

    expect(request).toHaveBeenNthCalledWith(1, '/collaboration/teams', {
      method: 'POST', body: JSON.stringify({ name: '联合研判', description: '防洪会商' }),
    });
    expect(request).toHaveBeenNthCalledWith(2, '/collaboration/teams/team%2F1/draft', {
      method: 'PUT',
      body: JSON.stringify({
        revision: 3,
        draft: {
          supervisor: { agent_id: 'forecast', responsibility: '统筹研判', tool_ids: ['forecast.read'], skill_names: ['forecast'], knowledge_source_ids: ['knowledge.rainfall'] },
          members: [{ agent_id: 'review', responsibility: '复核结论', tool_ids: ['review.read'], skill_names: [], knowledge_source_ids: [] }],
          tool_ids: ['forecast.read', 'review.read'], skill_names: ['forecast'], knowledge_source_ids: ['knowledge.rainfall'],
          max_steps: 8, max_parallel_members: 1, timeout_seconds: 600, failure_strategy: 'fail_fast', approval_policy_id: 'control-commands',
        },
      }),
    });
  });
});
