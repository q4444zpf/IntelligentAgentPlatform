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
});
