// @vitest-environment happy-dom
import { flushPromises, mount } from '@vue/test-utils';
import { defineComponent, h } from 'vue';
import { afterEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  echartsCoreImports: 0,
  mermaidImports: 0,
}));
const wrappers: Array<{ unmount: () => void }> = [];

vi.mock('mermaid', () => {
  mocks.mermaidImports += 1;
  return { default: {} };
});

vi.mock('echarts/core', () => {
  mocks.echartsCoreImports += 1;
  return {};
});

const MarkdownStub = defineComponent({
  props: { source: { required: true, type: String } },
  template: '<div data-answer-block="markdown">{{ source }}</div>',
});
const MermaidStub = defineComponent({
  props: { source: { required: true, type: String } },
  template: '<div data-answer-block="mermaid">{{ source }}</div>',
});
const EChartsStub = defineComponent({
  props: { source: { required: true, type: String } },
  template: '<div data-answer-block="echarts">{{ source }}</div>',
});

async function render(content: string, stubs = {}) {
  const { default: AssistantMessageContent } = await import('./AssistantMessageContent.vue');
  const wrapper = mount(AssistantMessageContent, {
    props: { content },
    global: {
      stubs: {
        SafeMarkdownBlock: MarkdownStub,
        ...stubs,
      },
    },
  });
  wrappers.push(wrapper);
  return wrapper;
}

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  vi.doUnmock('@/features/chat/answerBlocks');
  vi.resetModules();
  vi.restoreAllMocks();
  mocks.echartsCoreImports = 0;
  mocks.mermaidImports = 0;
});

describe('AssistantMessageContent', () => {
  it('renders parsed block types in their message order', async () => {
    const wrapper = await render('前言\n\n```mermaid\ngraph LR\nA-->B\n```\n\n结论\n\n```echarts\n{"series":[]}\n```', {
      MermaidBlock: MermaidStub,
      EChartsBlock: EChartsStub,
    });

    expect(wrapper.findAll('[data-answer-block]').map((block) => block.attributes('data-answer-block')))
      .toEqual(['markdown', 'mermaid', 'markdown', 'echarts']);
    expect(wrapper.findAll('[data-answer-block]').map((block) => block.element.textContent))
      .toEqual(['前言\n\n', 'graph LR\nA-->B\n', '\n结论\n\n', '{"series":[]}\n']);
  });

  it('does not load chart renderers for an ordinary Markdown message', async () => {
    const wrapper = await render('仅包含 **Markdown** 内容');
    await flushPromises();

    expect(wrapper.get('[data-answer-block="markdown"]').text()).toBe('仅包含 **Markdown** 内容');
    expect(wrapper.find('.mermaid-block').exists()).toBe(false);
    expect(wrapper.find('.echarts-block').exists()).toBe(false);
    expect(mocks.mermaidImports).toBe(0);
    expect(mocks.echartsCoreImports).toBe(0);
  });

  it('retries a failed specialized block when its source changes', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const RetriableMermaid = defineComponent({
      props: { source: { required: true, type: String } },
      setup(props) {
        if (props.source === 'broken\n') throw new Error('Mermaid setup failed');
        return () => h('div', { 'data-answer-block': 'mermaid' }, props.source);
      },
    });
    const wrapper = await render('开始\n\n```mermaid\nbroken\n```\n\n结束', {
      MermaidBlock: RetriableMermaid,
    });

    expect(wrapper.findAll('[data-answer-block="markdown"]').map((block) => block.element.textContent))
      .toEqual(['开始\n\n', '\n结束']);
    expect(wrapper.get('.source-fallback').text()).toContain('Mermaid 图表不可用');

    await wrapper.setProps({ content: '开始\n\n```mermaid\nrecovered\n```\n\n结束' });

    expect(wrapper.findAll('[data-answer-block="markdown"]').map((block) => block.element.textContent))
      .toEqual(['开始\n\n', '\n结束']);
    expect(wrapper.find('.source-fallback').exists()).toBe(false);
    expect(wrapper.get('[data-answer-block="mermaid"]').text()).toBe('recovered');
  });

  it('uses plain interpolation fallback when parsing throws', async () => {
    vi.doMock('@/features/chat/answerBlocks', async () => {
      const actual = await vi.importActual<typeof import('@/features/chat/answerBlocks')>('@/features/chat/answerBlocks');
      return {
        ...actual,
        parseAnswerBlocks: () => {
          throw new Error('parser failed');
        },
      };
    });
    vi.resetModules();

    const wrapper = await render('<img src=x onerror=alert(1)>');

    expect(wrapper.get('pre.answer-plain-fallback').text()).toBe('<img src=x onerror=alert(1)>');
    expect(wrapper.html()).not.toContain('<img');
  });
});
