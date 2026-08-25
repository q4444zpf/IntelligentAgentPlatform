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

describe('fixed authorization routes', () => {
  it('keeps an authenticated forbidden page available without a business permission', () => {
    const forbidden = routes.find((route) => route.path === '/403');

    expect(forbidden?.meta?.public).not.toBe(true);
    expect(forbidden?.meta?.permission).toBeUndefined();
    expect(String(forbidden?.component)).toContain('ForbiddenView');
  });
});
