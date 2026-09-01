import { MAX_MERMAID_BYTES } from './answerBlocks';

export class MermaidPolicyError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'MermaidPolicyError';
  }
}

export const MAX_MERMAID_LINES = 200;
export const MAX_MERMAID_STATEMENTS = 150;
export const MAX_MERMAID_NODES = 100;
export const MAX_MERMAID_EDGES = 120;
export const MAX_MERMAID_TOKENS = 1_000;

const HEADER = /^(?:graph|flowchart)\s+(?:TB|TD|BT|RL|LR)$/i;
const NODE = /^([A-Za-z_][A-Za-z0-9_-]*)(?:\s*(?:\[\[[^\]\r\n]*\]\]|\(\([^()\r\n]*\)\)|\[\([^\]\r\n]*\)\]|\(\[[^\]\r\n]*\]\)|\[[^\[\]\r\n]*\]|\([^()\r\n]*\)|\{[^{}\r\n]*\}))?$/u;
const EDGE = /^(.+?)\s*(-->|---)\s*(?:\|([^|\r\n]*)\|\s*)?(.+)$/u;
const RESERVED_STATEMENT = /^(?:click|classDef|class|style|linkStyle|subgraph|end|direction)\b/i;
const UNSAFE_SOURCE = [
  /%%\s*\{/i,
  /(?:^|\s)href\b/i,
  /(?:https?:|data:|javascript:|vbscript:|file:|\\\\|\/\/)/i,
  /(?:url\s*\(|@import|expression\s*\()/i,
  /(?:^|[,{\s])img\s*:/i,
  /@\s*\{/,
  /<\/?[A-Za-z!]/,
];

function policyError(message: string): never {
  throw new MermaidPolicyError(message);
}

function parseNode(value: string): string {
  const match = NODE.exec(value.trim());
  if (!match) policyError('图表源码仅支持基础节点与连线语句');
  return match[1];
}

export function validateMermaidSource(source: string): void {
  if (new TextEncoder().encode(source).byteLength > MAX_MERMAID_BYTES) {
    policyError('图表源码超过 100 KB 限制');
  }
  const lines = source.split(/\r?\n/);
  if (lines.length > MAX_MERMAID_LINES) policyError('图表源码行数超过 200 行限制');
  if (/^\s*---(?:\s*$|\r?\n)/.test(source)) policyError('图表源码不允许 YAML frontmatter');
  if (UNSAFE_SOURCE.some((pattern) => pattern.test(source))) policyError('图表源码包含不允许的资源或可执行语法');

  const tokenCount = source.match(/[\p{L}\p{N}_-]+|-->|---|[()[\]{}|]/gu)?.length ?? 0;
  if (tokenCount > MAX_MERMAID_TOKENS) policyError('图表源码词法单元超过 1,000 个限制');

  const statements = source
    .split(/\r?\n|;/)
    .map((statement) => statement.trim())
    .filter((statement) => statement && !statement.startsWith('%%'));
  const [header, ...body] = statements;
  if (!header || !HEADER.test(header)) policyError('图表源码仅支持 graph 或 flowchart');
  if (body.length > MAX_MERMAID_STATEMENTS) policyError('图表源码语句超过 150 条限制');

  const nodes = new Set<string>();
  let edges = 0;
  for (const statement of body) {
    if (RESERVED_STATEMENT.test(statement)) policyError('图表源码包含不支持的交互或样式语句');
    const edge = EDGE.exec(statement);
    if (edge) {
      nodes.add(parseNode(edge[1]));
      nodes.add(parseNode(edge[4]));
      edges += 1;
    } else {
      nodes.add(parseNode(statement));
    }
    if (nodes.size > MAX_MERMAID_NODES) policyError('图表源码节点超过 100 个限制');
    if (edges > MAX_MERMAID_EDGES) policyError('图表源码连线超过 120 条限制');
  }
}
