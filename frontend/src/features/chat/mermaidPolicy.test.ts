import { describe, expect, it } from 'vitest';
import {
  MAX_MERMAID_EDGES,
  MAX_MERMAID_LINES,
  MAX_MERMAID_NODES,
  MAX_MERMAID_STATEMENTS,
  MAX_MERMAID_TOKENS,
  MermaidPolicyError,
  validateMermaidSource,
} from './mermaidPolicy';

function diagram(statements: string[]): string {
  return ['flowchart LR', ...statements].join('\n');
}

describe('validateMermaidSource', () => {
  it('accepts conservative graph and flowchart node/edge syntax', () => {
    expect(() => validateMermaidSource([
      'flowchart LR',
      'A[入库流量] -->|演算| B{是否超汛限}',
      'B --> C(生成预泄方案)',
    ].join('\n'))).not.toThrow();
    expect(() => validateMermaidSource('graph TD\nA---B')).not.toThrow();
  });

  it.each([
    ['YAML frontmatter', '---\ntitle: unsafe\n---\nflowchart LR\nA-->B'],
    ['configuration directive', '%%{init: {"flowchart":{"htmlLabels":true}}}%%\nflowchart LR\nA-->B'],
    ['click navigation', 'flowchart LR\nA-->B\nclick A href "https://attacker.example"'],
    ['image node', 'flowchart LR\nA@{ img: "https://attacker.example/pixel.png", label: "A" }'],
    ['raw HTML', 'flowchart LR\nA[<script>alert(1)</script>]'],
    ['CSS resource', 'flowchart LR\nA[url(https://attacker.example/pixel.png)]'],
    ['styling statement', 'flowchart LR\nA-->B\nclassDef unsafe fill:red'],
    ['unsupported diagram kind', 'sequenceDiagram\nAlice->>Bob: hello'],
  ])('rejects %s before Mermaid can parse it', (_, source) => {
    expect(() => validateMermaidSource(source)).toThrow(MermaidPolicyError);
  });

  it('enforces the line boundary', () => {
    expect(() => validateMermaidSource(['flowchart LR', ...Array(MAX_MERMAID_LINES - 1).fill('')].join('\n')))
      .not.toThrow();
    expect(() => validateMermaidSource(['flowchart LR', ...Array(MAX_MERMAID_LINES).fill('')].join('\n')))
      .toThrow('行数');
  });

  it('enforces the statement boundary independently of nodes and edges', () => {
    expect(() => validateMermaidSource(diagram(Array(MAX_MERMAID_STATEMENTS).fill('A[重复节点]'))))
      .not.toThrow();
    expect(() => validateMermaidSource(diagram(Array(MAX_MERMAID_STATEMENTS + 1).fill('A[重复节点]'))))
      .toThrow('语句');
  });

  it('enforces the unique-node boundary', () => {
    const nodes = (count: number) => Array.from({ length: count }, (_, index) => `N${index}[节点 ${index}]`);

    expect(() => validateMermaidSource(diagram(nodes(MAX_MERMAID_NODES)))).not.toThrow();
    expect(() => validateMermaidSource(diagram(nodes(MAX_MERMAID_NODES + 1)))).toThrow('节点');
  });

  it('enforces the edge boundary', () => {
    expect(() => validateMermaidSource(diagram(Array(MAX_MERMAID_EDGES).fill('A-->B')))).not.toThrow();
    expect(() => validateMermaidSource(diagram(Array(MAX_MERMAID_EDGES + 1).fill('A-->B')))).toThrow('连线');
  });

  it('enforces the lexical-token boundary', () => {
    const label = Array(MAX_MERMAID_TOKENS + 1).fill('词').join(' ');

    expect(() => validateMermaidSource(`flowchart LR\nA[${label}]`)).toThrow('词法');
  });
});
