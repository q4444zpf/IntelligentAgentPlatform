// @vitest-environment happy-dom
import { mount } from '@vue/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  copyText: vi.fn<(text: string) => Promise<void>>().mockResolvedValue(undefined),
}));
const wrappers: Array<{ unmount: () => void }> = [];

vi.mock('@/utils/clipboard', () => ({
  copyText: mocks.copyText,
}));

import SafeMarkdownBlock from './SafeMarkdownBlock.vue';

function render(source: string) {
  const wrapper = mount(SafeMarkdownBlock, { props: { source } });
  wrappers.push(wrapper);
  return wrapper;
}

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  mocks.copyText.mockClear();
});

describe('SafeMarkdownBlock', () => {
  it('copies the exact fenced code source', async () => {
    const wrapper = render('```ts\nconst level = 12.3;\n```');

    await wrapper.get('button[aria-label="复制代码"]').trigger('click');

    expect(mocks.copyText).toHaveBeenCalledWith('const level = 12.3;\n');
  });

  it('replaces a failed image without hiding adjacent paragraphs', async () => {
    const wrapper = render('前置说明\n\n![水位图](https://example.com/level.png)\n\n后续说明');

    await wrapper.get('img').trigger('error');

    expect(wrapper.find('img').exists()).toBe(false);
    expect(wrapper.get('.image-fallback').text()).toContain('图片加载失败');
    expect(wrapper.text()).toContain('前置说明');
    expect(wrapper.text()).toContain('后续说明');
  });

  it('removes listeners from replaced code and image elements after a prop update', async () => {
    const wrapper = render('```ts\nconst stale = true;\n```\n\n![旧图](https://example.com/old.png)');
    const staleButton = wrapper.get<HTMLButtonElement>('button[aria-label="复制代码"]').element;
    const staleImage = wrapper.get<HTMLImageElement>('img').element;
    const replaceWith = vi.spyOn(staleImage, 'replaceWith');

    await wrapper.setProps({
      source: '```ts\nconst current = true;\n```\n\n![新图](https://example.com/new.png)',
    });
    staleButton.click();
    staleImage.dispatchEvent(new Event('error'));

    expect(mocks.copyText).not.toHaveBeenCalled();
    expect(replaceWith).not.toHaveBeenCalled();
    await wrapper.get('button[aria-label="复制代码"]').trigger('click');
    expect(mocks.copyText).toHaveBeenCalledWith('const current = true;\n');
  });

  it('removes listeners from owned elements on unmount', () => {
    const wrapper = render('```ts\nconst stale = true;\n```\n\n![旧图](https://example.com/old.png)');
    const staleButton = wrapper.get<HTMLButtonElement>('button[aria-label="复制代码"]').element;
    const staleImage = wrapper.get<HTMLImageElement>('img').element;
    const replaceWith = vi.spyOn(staleImage, 'replaceWith');

    wrapper.unmount();
    staleButton.click();
    staleImage.dispatchEvent(new Event('error'));

    expect(mocks.copyText).not.toHaveBeenCalled();
    expect(replaceWith).not.toHaveBeenCalled();
  });
});
