// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils';
import { defineComponent, ref } from 'vue';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ArtifactInfo } from '@/api/artifacts';
import AssistantArtifactCards from './AssistantArtifactCards.vue';
import HtmlArtifactPreview from './HtmlArtifactPreview.vue';

const mocks = vi.hoisted(() => ({
  download: vi.fn(),
  preview: vi.fn(),
}));

vi.mock('@/api/artifacts', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/api/artifacts')>();
  return {
    ...original,
    artifactsApi: { ...original.artifactsApi, download: mocks.download, preview: mocks.preview },
  };
});

const firstArtifact: ArtifactInfo = {
  id: 'artifact-1',
  filename: 'flood-report.html',
  content_type: 'text/html; charset=utf-8',
  run_id: 'run-1',
  size_bytes: 1536,
  created_at: '2026-08-28T01:02:03Z',
  status: 'available',
};

const secondArtifact: ArtifactInfo = {
  id: 'artifact-2',
  filename: 'dispatch-plan.htm',
  content_type: 'application/octet-stream',
  run_id: 'run-2',
  size_bytes: 2048,
  created_at: '2026-08-28T02:03:04Z',
  status: 'available',
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const PreviewHarness = defineComponent({
  components: { AssistantArtifactCards, HtmlArtifactPreview },
  setup() {
    const selected = ref<ArtifactInfo | null>(null);
    return { artifacts: [firstArtifact], selected };
  },
  template: `
    <section data-testid="chat">对话仍在</section>
    <AssistantArtifactCards :artifacts="artifacts" @preview="selected = $event" />
    <HtmlArtifactPreview v-if="selected" :artifact="selected" @close="selected = null" />
  `,
});

beforeEach(() => {
  mocks.download.mockReset();
  mocks.preview.mockReset();
  mocks.preview.mockResolvedValue({ url: 'https://objects.example/default-preview', expires_in: 300 });
});

afterEach(() => {
  vi.restoreAllMocks();
  document.body.innerHTML = '';
});

describe('HTML Artifact card and preview', () => {
  it('opens a fresh signed URL in a scriptless sandbox when the user clicks the run-linked card', async () => {
    mocks.preview.mockResolvedValue({ url: 'https://objects.example/signed', expires_in: 300 });
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    const wrapper = mount(PreviewHarness);

    expect(wrapper.text()).toContain('flood-report.html');
    expect(wrapper.text()).toContain('text/html; charset=utf-8');
    expect(wrapper.text()).toContain('1.5 KB');
    await wrapper.get('[aria-label="预览 flood-report.html"]').trigger('click');
    await flushPromises();

    const iframe = wrapper.get('iframe');
    expect(iframe.attributes('sandbox')).toBe('');
    expect(iframe.attributes('referrerpolicy')).toBe('no-referrer');
    expect(iframe.attributes('allow')).toBeUndefined();
    expect(iframe.attributes('srcdoc')).toBeUndefined();
    expect(iframe.attributes('src')).toBe('https://objects.example/signed');
    expect(mocks.preview).toHaveBeenCalledWith('artifact-1', expect.any(AbortSignal));
    expect(mocks.download).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it('refreshes with a new signed URL and toggles the full viewport surface', async () => {
    mocks.preview
      .mockResolvedValueOnce({ url: 'https://objects.example/first' })
      .mockResolvedValueOnce({ url: 'https://objects.example/refreshed' });
    const wrapper = mount(HtmlArtifactPreview, { props: { artifact: firstArtifact } });
    await flushPromises();

    await wrapper.get('[aria-label="刷新 HTML 预览"]').trigger('click');
    await flushPromises();
    expect(wrapper.get('iframe').attributes('src')).toBe('https://objects.example/refreshed');

    await wrapper.get('[aria-label="全屏 HTML 预览"]').trigger('click');
    expect(wrapper.classes()).toContain('is-fullscreen');
    await wrapper.get('[aria-label="退出全屏 HTML 预览"]').trigger('click');
    expect(wrapper.classes()).not.toContain('is-fullscreen');
    wrapper.unmount();
  });

  it('aborts a superseded request and never lets its late result replace the selected artifact URL', async () => {
    const oldRequest = deferred<{ url: string }>();
    const newRequest = deferred<{ url: string }>();
    const signals: AbortSignal[] = [];
    mocks.preview.mockImplementation((_id: string, signal: AbortSignal) => {
      signals.push(signal);
      return signals.length === 1 ? oldRequest.promise : newRequest.promise;
    });
    const wrapper = mount(HtmlArtifactPreview, { props: { artifact: firstArtifact } });

    await wrapper.setProps({ artifact: secondArtifact });
    expect(signals[0].aborted).toBe(true);
    newRequest.resolve({ url: 'https://objects.example/selected' });
    await flushPromises();
    oldRequest.resolve({ url: 'https://objects.example/stale' });
    await flushPromises();

    expect(wrapper.get('iframe').attributes('src')).toBe('https://objects.example/selected');
    expect(wrapper.get('iframe').attributes('title')).toBe('HTML 预览：dispatch-plan.htm');
    wrapper.unmount();
  });

  it('does not show an aborted superseded request as a preview failure', async () => {
    mocks.preview.mockImplementationOnce((_id: string, signal: AbortSignal) => new Promise((_resolve, reject) => {
      signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    }));
    mocks.preview.mockResolvedValueOnce({ url: 'https://objects.example/next' });
    const wrapper = mount(HtmlArtifactPreview, { props: { artifact: firstArtifact } });

    await wrapper.setProps({ artifact: secondArtifact });
    await flushPromises();

    expect(wrapper.get('iframe').attributes('src')).toBe('https://objects.example/next');
    expect(wrapper.text()).not.toContain('HTML 预览加载失败');
    wrapper.unmount();
  });

  it('downloads through a temporary anchor and removes it after the click', async () => {
    mocks.download.mockResolvedValueOnce({ url: 'https://objects.example/download' });
    const clickedAnchors: HTMLAnchorElement[] = [];
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function click(this: HTMLAnchorElement) {
      clickedAnchors.push(this);
    });
    const wrapper = mount(HtmlArtifactPreview, { props: { artifact: firstArtifact } });
    await flushPromises();

    await wrapper.get('[aria-label="下载 flood-report.html"]').trigger('click');
    await flushPromises();

    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(clickedAnchors[0].href).toBe('https://objects.example/download');
    expect(clickedAnchors[0].download).toBe('flood-report.html');
    expect(document.body.contains(clickedAnchors[0])).toBe(false);
    wrapper.unmount();
  });

  it('aborts a stale download on artifact switch and ignores its late URL before downloading the new artifact', async () => {
    const staleDownload = deferred<{ url: string }>();
    let staleSignal: AbortSignal | undefined;
    mocks.download
      .mockImplementationOnce((_id: string, signal: AbortSignal) => {
        staleSignal = signal;
        return staleDownload.promise;
      })
      .mockResolvedValueOnce({ url: 'https://objects.example/b-download' });
    const clickedAnchors: HTMLAnchorElement[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function click(this: HTMLAnchorElement) {
      clickedAnchors.push(this);
    });
    const wrapper = mount(HtmlArtifactPreview, { props: { artifact: firstArtifact } });
    await flushPromises();

    await wrapper.get('[aria-label="下载 flood-report.html"]').trigger('click');
    await wrapper.setProps({ artifact: secondArtifact });
    await flushPromises();
    expect(staleSignal?.aborted).toBe(true);

    staleDownload.resolve({ url: 'https://objects.example/a-stale-download' });
    await flushPromises();
    expect(clickedAnchors).toHaveLength(0);
    expect(wrapper.text()).not.toContain('HTML 文件下载失败');

    await wrapper.get('[aria-label="下载 dispatch-plan.htm"]').trigger('click');
    await flushPromises();
    expect(clickedAnchors).toHaveLength(1);
    expect(clickedAnchors[0].href).toBe('https://objects.example/b-download');
    expect(clickedAnchors[0].download).toBe('dispatch-plan.htm');
    wrapper.unmount();
  });

  it('ignores a stale download rejection after switching artifacts', async () => {
    const staleDownload = deferred<{ url: string }>();
    let staleSignal: AbortSignal | undefined;
    mocks.download
      .mockImplementationOnce((_id: string, signal: AbortSignal) => {
        staleSignal = signal;
        return staleDownload.promise;
      });
    const wrapper = mount(HtmlArtifactPreview, { props: { artifact: firstArtifact } });
    await flushPromises();

    await wrapper.get('[aria-label="下载 flood-report.html"]').trigger('click');
    await wrapper.setProps({ artifact: secondArtifact });
    await flushPromises();
    expect(staleSignal?.aborted).toBe(true);

    staleDownload.reject(new Error('stale A failure'));
    await flushPromises();
    expect(wrapper.text()).not.toContain('HTML 文件下载失败');
    expect(wrapper.get('iframe').attributes('title')).toBe('HTML 预览：dispatch-plan.htm');
    wrapper.unmount();
  });

  it('opens a controlled same-app viewer containing only the artifact id', async () => {
    mocks.preview.mockResolvedValue({ url: 'https://objects.example/signed?secret=token' });
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    const wrapper = mount(HtmlArtifactPreview, { props: { artifact: firstArtifact } });
    await flushPromises();

    await wrapper.get('[aria-label="在新窗口打开 HTML 预览"]').trigger('click');

    expect(openSpy).toHaveBeenCalledWith('/artifacts/artifact-1/preview', '_blank', 'noopener,noreferrer');
    expect(openSpy.mock.calls[0][0]).not.toContain('objects.example');
    expect(openSpy.mock.calls[0][0]).not.toContain('secret');
    wrapper.unmount();
  });

  it('shows retry and close after failure while the surrounding chat remains mounted', async () => {
    mocks.preview
      .mockRejectedValueOnce(new Error('temporary outage'))
      .mockResolvedValueOnce({ url: 'https://objects.example/retry' });
    const wrapper = mount(PreviewHarness);
    await wrapper.get('[aria-label="预览 flood-report.html"]').trigger('click');
    await flushPromises();

    expect(wrapper.text()).toContain('HTML 预览加载失败');
    expect(wrapper.get('[data-testid="chat"]').text()).toBe('对话仍在');
    await wrapper.get('[aria-label="重试 HTML 预览"]').trigger('click');
    await flushPromises();
    expect(wrapper.get('iframe').attributes('src')).toBe('https://objects.example/retry');

    await wrapper.get('[aria-label="关闭 HTML 预览"]').trigger('click');
    expect(wrapper.findComponent(HtmlArtifactPreview).exists()).toBe(false);
    expect(wrapper.get('[data-testid="chat"]').text()).toBe('对话仍在');
    wrapper.unmount();
  });

  it('emits close and aborts its pending request when unmounted', async () => {
    const pending = deferred<{ url: string }>();
    let signal: AbortSignal | undefined;
    mocks.preview.mockImplementation((_id: string, requestSignal: AbortSignal) => {
      signal = requestSignal;
      return pending.promise;
    });
    const wrapper = mount(HtmlArtifactPreview, { props: { artifact: firstArtifact } });

    await wrapper.get('[aria-label="关闭 HTML 预览"]').trigger('click');
    expect(wrapper.emitted('close')).toEqual([[]]);
    wrapper.unmount();
    expect(signal?.aborted).toBe(true);
  });
});
