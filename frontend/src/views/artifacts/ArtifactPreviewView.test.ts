// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  artifactId: 'artifact-1' as string | string[],
  preview: vi.fn(),
}));

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { artifactId: mocks.artifactId } }),
}));

vi.mock('@/api/artifacts', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/api/artifacts')>();
  return {
    ...original,
    artifactsApi: { ...original.artifactsApi, preview: mocks.preview },
  };
});

import ArtifactPreviewView from './ArtifactPreviewView.vue';

beforeEach(() => {
  mocks.artifactId = 'artifact-1';
  mocks.preview.mockReset();
});

describe('ArtifactPreviewView', () => {
  it('reacquires an authorized URL from the route id and keeps it in a zero-capability iframe', async () => {
    mocks.preview.mockResolvedValue({ url: 'https://objects.example/signed?secret=token', expires_in: 300 });
    const wrapper = mount(ArtifactPreviewView);
    await flushPromises();

    expect(mocks.preview).toHaveBeenCalledWith('artifact-1', expect.any(AbortSignal));
    const iframe = wrapper.get('iframe');
    expect(iframe.attributes('sandbox')).toBe('');
    expect(iframe.attributes('referrerpolicy')).toBe('no-referrer');
    expect(iframe.attributes('allow')).toBeUndefined();
    expect(iframe.attributes('srcdoc')).toBeUndefined();
    expect(iframe.attributes('src')).toBe('https://objects.example/signed?secret=token');
    expect(wrapper.text()).not.toContain('secret=token');
    wrapper.unmount();
  });

  it('does not request a preview for a non-scalar route parameter', async () => {
    mocks.artifactId = ['artifact-1', 'extra'];
    const wrapper = mount(ArtifactPreviewView);
    await flushPromises();

    expect(mocks.preview).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain('HTML 预览地址无效');
    wrapper.unmount();
  });
});
