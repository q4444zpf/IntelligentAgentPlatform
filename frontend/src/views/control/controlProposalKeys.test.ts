import { describe, expect, it, vi } from 'vitest';
import { createControlProposalKeys } from './controlProposalKeys';

describe('control proposal keys', () => {
  it('keeps each unresolved draft when another draft is submitted between retries', () => {
    const uuid = vi.fn().mockReturnValueOnce('key-a').mockReturnValueOnce('key-b').mockReturnValueOnce('key-a-next');
    const keys = createControlProposalKeys(uuid);
    expect(keys.keyFor('scope-a', 'draft-a')).toBe('key-a');
    expect(keys.keyFor('scope-a', 'draft-b')).toBe('key-b');
    expect(keys.keyFor('scope-a', 'draft-a')).toBe('key-a');
    keys.clear('scope-a', 'draft-a');
    expect(keys.keyFor('scope-a', 'draft-a')).toBe('key-a-next');
    expect(keys.keyFor('scope-a', 'draft-b')).toBe('key-b');
  });
});
