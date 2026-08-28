import { describe, expect, it } from 'vitest';
import { parseAnswerBlocks } from './answerBlocks';

describe('parseAnswerBlocks', () => {
  it('preserves ordered markdown, Mermaid, and ECharts source', () => {
    expect(parseAnswerBlocks('前言\n\n``` Mermaid \ngraph LR\nA-->B\n```\n\n结论\n\n```echarts\n{"series":[]}\n```')).toEqual([
      { type: 'markdown', source: '前言\n\n' },
      { type: 'mermaid', source: 'graph LR\nA-->B\n' },
      { type: 'markdown', source: '\n结论\n\n' },
      { type: 'echarts', source: '{"series":[]}\n' },
    ]);
  });

  it('keeps unknown and unclosed fences as markdown', () => {
    expect(parseAnswerBlocks('```python\nprint(1)\n```')).toEqual([
      { type: 'markdown', source: '```python\nprint(1)\n```' },
    ]);
    expect(parseAnswerBlocks('```mermaid\ngraph LR')).toEqual([
      { type: 'markdown', source: '```mermaid\ngraph LR' },
    ]);
  });

  it.each([
    ['mermaid title', 'graph LR\nA-->B'],
    ['echarts json', '{"series":[]}'],
  ])('keeps a fence with the non-exact info string %s as markdown', (info, body) => {
    const source = `\`\`\`${info}\n${body}\n\`\`\``;

    expect(parseAnswerBlocks(source)).toEqual([
      { type: 'markdown', source },
    ]);
  });

  it('does not emit empty markdown segments around a special fence', () => {
    expect(parseAnswerBlocks('```echarts\n{"series":[]}\n```')).toEqual([
      { type: 'echarts', source: '{"series":[]}\n' },
    ]);
  });

  it('specializes only the first six matching blocks', () => {
    const source = Array.from({ length: 7 }, (_, index) => `\`\`\`mermaid\ngraph LR\nA${index}-->B\n\`\`\``).join('\n');
    const blocks = parseAnswerBlocks(source);

    expect(blocks.filter((block) => block.type === 'mermaid')).toHaveLength(6);
    expect(blocks.at(-1)).toMatchObject({ type: 'markdown' });
    expect(blocks.at(-1)?.source).toContain('A6-->B');
  });

  it('specializes a fence whose closing delimiter has three leading spaces', () => {
    expect(parseAnswerBlocks('```mermaid\ngraph LR\nA-->B\n   ```')).toEqual([
      { type: 'mermaid', source: 'graph LR\nA-->B\n' },
    ]);
  });

  it('keeps a fence with a four-space-indented closing delimiter as markdown', () => {
    const source = '```mermaid\ngraph LR\nA-->B\n    ```';

    expect(parseAnswerBlocks(source)).toEqual([
      { type: 'markdown', source },
    ]);
  });

  it('specializes tilde fences', () => {
    expect(parseAnswerBlocks('~~~echarts\n{"series":[]}\n~~~')).toEqual([
      { type: 'echarts', source: '{"series":[]}\n' },
    ]);
  });
});
