import { describe, expect, it, vi } from 'vitest';
import { createControlActionKeys } from './controlActionKeys';

describe('control action keys', () => {
  it('reuses a UUID by command and kind until a terminal response clears it', () => {
    const uuid = vi.fn().mockReturnValueOnce('key-1').mockReturnValueOnce('key-2').mockReturnValueOnce('key-3');
    const keys = createControlActionKeys(uuid);
    expect(keys.keyFor('command-1', 'execute')).toBe('key-1');
    expect(keys.keyFor('command-1', 'execute')).toBe('key-1');
    expect(keys.keyFor('command-1', 'rollback')).toBe('key-2');
    keys.clear('command-1', 'execute');
    expect(keys.keyFor('command-1', 'execute')).toBe('key-3');
  });
});
