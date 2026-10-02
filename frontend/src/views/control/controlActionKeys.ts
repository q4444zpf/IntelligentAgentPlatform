export type ControlActionKind = 'execute' | 'rollback';

export function createControlActionKeys(uuid: () => string = () => crypto.randomUUID()) {
  const keys = new Map<string, string>();
  const identity = (commandId: string, kind: ControlActionKind) => `${commandId.length}:${commandId}:${kind}`;
  return {
    keyFor(commandId: string, kind: ControlActionKind) {
      const id = identity(commandId, kind);
      let key = keys.get(id);
      if (!key) { key = uuid(); keys.set(id, key); }
      return key;
    },
    clear(commandId: string, kind: ControlActionKind) { keys.delete(identity(commandId, kind)); },
  };
}

export const controlActionKeys = createControlActionKeys();
