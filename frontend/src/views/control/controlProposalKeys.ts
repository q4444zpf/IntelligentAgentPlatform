export function createControlProposalKeys(uuid: () => string = () => crypto.randomUUID()) {
  const pending = new Map<string, Map<string, string>>();
  return {
    keyFor(scope: string, signature: string) {
      let drafts = pending.get(scope);
      if (!drafts) {
        drafts = new Map();
        pending.set(scope, drafts);
      }
      let key = drafts.get(signature);
      if (!key) {
        key = uuid();
        drafts.set(signature, key);
      }
      return key;
    },
    clear(scope: string, signature: string) {
      const drafts = pending.get(scope);
      drafts?.delete(signature);
      if (drafts?.size === 0) pending.delete(scope);
    },
  };
}

export const controlProposalKeys = createControlProposalKeys();
