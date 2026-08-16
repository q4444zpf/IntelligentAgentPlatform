import { describe, expect, it } from 'vitest';

import { routes } from './routes';

describe('artifact route', () => {
  it('uses the dedicated artifact view instead of the integration placeholder', () => {
    const root = routes.find((route) => route.path === '/');
    const artifact = root?.children?.find((route) => route.path === 'artifacts');

    expect(artifact?.meta?.module).toBeUndefined();
    expect(String(artifact?.component)).toContain('ArtifactListView');
  });
});
