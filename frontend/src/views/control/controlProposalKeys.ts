export function createControlProposalKeys(uuid: () => string = () => crypto.randomUUID()) {
  let pending: { signature: string; key: string } | null = null;
  return {
    keyFor(signature: string) {
      if (pending?.signature !== signature) pending = { signature, key: uuid() };
      return pending.key;
    },
    clear(signature: string) {
      if (pending?.signature === signature) pending = null;
    },
  };
}

export const controlProposalKeys = createControlProposalKeys();
