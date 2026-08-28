import { describe, expect, it } from 'vitest';

import { routes } from './routes';

describe('artifact route', () => {
  it('uses the dedicated artifact view instead of the integration placeholder', () => {
    const root = routes.find((route) => route.path === '/');
    const artifact = root?.children?.find((route) => route.path === 'artifacts');

    expect(artifact?.meta?.module).toBeUndefined();
    expect(String(artifact?.component)).toContain('ArtifactListView');
  });

  it('registers the controlled artifact viewer before the catch-all route', () => {
    const previewIndex = routes.findIndex((route) => route.path === '/artifacts/:artifactId/preview');
    const catchAllIndex = routes.findIndex((route) => route.path === '/:pathMatch(.*)*');
    const preview = routes[previewIndex];

    expect(previewIndex).toBeGreaterThanOrEqual(0);
    expect(previewIndex).toBeLessThan(catchAllIndex);
    expect(preview?.meta?.permission).toBe('platform:view');
    expect(String(preview?.component)).toContain('ArtifactPreviewView');
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
