import MarkdownIt from 'markdown-it';

export type AnswerBlock =
  | { type: 'markdown'; source: string }
  | { type: 'mermaid'; source: string }
  | { type: 'echarts'; source: string };

export const MAX_SPECIAL_BLOCKS = 6;
export const MAX_MERMAID_BYTES = 100 * 1024;
export const MAX_ECHARTS_BYTES = 512 * 1024;

const tokenizer = new MarkdownIt({ html: true });

function lineOffsets(source: string): number[] {
  const offsets = [0];
  for (let index = 0; index < source.length; index += 1) {
    if (source[index] === '\n') offsets.push(index + 1);
  }
  offsets.push(source.length);
  return offsets;
}

function pushMarkdown(blocks: AnswerBlock[], source: string) {
  if (!source) return;
  const previous = blocks.at(-1);
  if (previous?.type === 'markdown') previous.source += source;
  else blocks.push({ type: 'markdown', source });
}

export function parseAnswerBlocks(content: string): AnswerBlock[] {
  if (!content) return [];
  const offsets = lineOffsets(content);
  const tokens = tokenizer.parse(content, {});
  const candidates = tokens.filter((token) => {
    const language = token.info.trim().toLowerCase();
    return token.type === 'fence' && token.map && (language === 'mermaid' || language === 'echarts');
  });
  const blocks: AnswerBlock[] = [];
  let cursor = 0;
  let specialized = 0;

  for (const token of candidates) {
    const [startLine, endLine] = token.map!;
    const start = offsets[startLine];
    const end = offsets[Math.min(endLine, offsets.length - 1)];
    const marker = token.markup;
    const closingLine = content
      .slice(offsets[endLine - 1], offsets[endLine] ?? content.length)
      .replace(/\r?\n$/, '');
    const markerCharacter = marker[0]?.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const closing = marker
      ? new RegExp(`^ {0,3}${markerCharacter}{${marker.length},}[ \\t]*$`).test(closingLine)
      : false;
    if (!closing || specialized >= MAX_SPECIAL_BLOCKS) continue;

    pushMarkdown(blocks, content.slice(cursor, start));
    blocks.push({ type: token.info.trim().toLowerCase() as 'mermaid' | 'echarts', source: token.content });
    cursor = end;
    specialized += 1;
  }
  pushMarkdown(blocks, content.slice(cursor));
  return blocks;
}
