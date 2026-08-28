// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest';
import { renderSafeMarkdown } from './safeMarkdown';

describe('renderSafeMarkdown', () => {
  it('renders common Markdown and normalizes external links', () => {
    const html = renderSafeMarkdown('# 水情\n\n- 水位 `12.3`\n\n|站点|值|\n|-|-|\n|飞来峡|12.3|\n\n[资料](https://example.com)');

    expect(html).toContain('<h1>水情</h1>');
    expect(html).toContain('<ul>');
    expect(html).toContain('<code>12.3</code>');
    expect(html).toContain('<table>');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
  });

  it('renders line breaks and safe remote images', () => {
    const html = renderSafeMarkdown('第一行\n第二行\n\n![水位图](https://example.com/level.png)');

    expect(html).toContain('第一行<br>\n第二行');
    expect(html).toContain('src="https://example.com/level.png"');
    expect(html).toContain('loading="lazy"');
    expect(html).toContain('referrerpolicy="no-referrer"');
  });

  it.each([
    '<script>alert(1)</script>',
    '<img src=x onerror=alert(1)>',
    '<form action="https://evil.test"><input></form>',
    '<svg><a href="javascript:alert(1)"><text>x</text></a></svg>',
    '[x](javascript:alert(1))',
    '![x](data:text/html;base64,PHNjcmlwdD4=)',
    '[x](//evil.test/path)',
  ])('removes executable content from %s', (source) => {
    const html = renderSafeMarkdown(source).toLowerCase();

    expect(html).not.toMatch(/script|onerror|<form|<input|javascript:|data:|href="\/\/|src="\/\//);
  });
});
