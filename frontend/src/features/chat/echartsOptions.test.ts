import { afterEach, describe, expect, it, vi } from 'vitest';
import { EChartsValidationError, parseEChartsOptions } from './echartsOptions';

function nestedObjects(layers: number): unknown {
  let result: unknown = 0;
  for (let index = 0; index < layers; index += 1) {
    result = { child: result };
  }
  return result;
}

afterEach(() => {
  expect((Object.prototype as Record<string, unknown>).polluted).toBeUndefined();
  vi.restoreAllMocks();
});

describe('parseEChartsOptions', () => {
  it('copies the supported chart schema and enforces non-HTML, non-animated rendering', () => {
    const option = parseEChartsOptions(JSON.stringify({
      title: { text: '未来 24 小时水位', left: 'center' },
      legend: { data: ['飞来峡'], top: 32 },
      tooltip: { trigger: 'axis' },
      grid: { left: 48, right: 24, containLabel: true },
      xAxis: { type: 'category', data: ['08:00', '09:00'] },
      yAxis: { type: 'value', name: '水位（m）' },
      dataset: { source: [['时刻', '水位'], ['08:00', 12.3]] },
      series: [
        { type: 'line', name: '飞来峡', data: [12.3, 12.8], smooth: true },
        { type: 'bar', data: [3, 4], barWidth: 12 },
        { type: 'scatter', data: [[1, 2]], symbol: 'circle' },
      ],
    }));

    expect(option).toMatchObject({
      animation: false,
      tooltip: { trigger: 'axis', renderMode: 'richText' },
      xAxis: { type: 'category', data: ['08:00', '09:00'] },
      series: [
        { type: 'line', data: [12.3, 12.8] },
        { type: 'bar', data: [3, 4] },
        { type: 'scatter', data: [[1, 2]], symbol: 'circle' },
      ],
    });
  });

  it.each([
    ['[]', '对象'],
    ['{"series":null}', '数组'],
    [JSON.stringify({ series: Array.from({ length: 21 }, () => ({ data: [] })) }), '20'],
    [JSON.stringify({ series: [{ data: Array.from({ length: 20_001 }, () => 1) }] }), '20,000'],
    ['{"__proto__":{"polluted":true}}', '危险字段'],
    ['{"series":[{"formatter":"function(){return 1}"}]}', 'formatter'],
  ])('rejects unsafe or excessive option %s', (source, message) => {
    expect(() => parseEChartsOptions(source)).toThrow(message);
  });

  it('rejects excessive nesting before invoking JSON.parse', () => {
    const parse = vi.spyOn(JSON, 'parse');

    expect(() => parseEChartsOptions(JSON.stringify(nestedObjects(21)))).toThrow('深度');
    expect(parse).not.toHaveBeenCalled();
  });

  it('does not count structural characters inside JSON strings as nesting', () => {
    expect(parseEChartsOptions('{"title":{"text":"[{\\\"not a container\\\"}]"},"series":[]}'))
      .toMatchObject({ title: { text: '[{"not a container"}]' } });
  });

  it('rejects a UTF-8 source above the 512 KB boundary before parsing', () => {
    const source = `"${'水'.repeat(174_764)}"`;

    expect(() => parseEChartsOptions(source)).toThrow(EChartsValidationError);
    expect(() => parseEChartsOptions(source)).toThrow('512 KB');
  });

  it.each([
    ['{"constructor":{"polluted":true}}', '危险字段'],
    ['{"prototype":{"polluted":true}}', '危险字段'],
    ['{"labelFormatter":"value"}', 'formatter'],
    ['{"graphic":{"elements":[{"onclick":"alert(1)"}]}}', 'onclick'],
    ['{"dataset":{"source":"https://example.com/data.csv"}}', '外部'],
    ['{"series":[{"symbol":"image://https://example.com/marker.png"}]}', 'image'],
    ['{"series":[{"symbol":" image:////example.com/marker.png "}]}', 'image'],
    ['{"series":[{"symbol":"image://data:image/svg+xml,unsafe"}]}', 'image'],
    ['{"graphic":{"elements":[{"style":{"image":" https://example.com/marker.png "}}]}}', '外部'],
    ['{"graphic":{"elements":[{"style":{"image":"javascript:alert(1)"}}]}}', '外部'],
    ['{"dataset":{"source":" //example.com/data.csv "}}', '外部'],
  ])('rejects prohibited ECharts content %s', (source, message) => {
    expect(() => parseEChartsOptions(source)).toThrow(message);
  });

  it.each([
    ['image-backed background', { backgroundColor: { image: 'https://attacker.example/pixel.png' } }],
    ['timeline branch', { timeline: { data: ['now'] }, options: [{ series: [] }] }],
    ['nested base option', { baseOption: { series: Array.from({ length: 21 }, () => ({ data: [] })) } }],
    ['responsive media option', { media: [{ option: { series: [{ type: 'line', data: [] }] } }] }],
    ['title navigation', { title: { text: 'report', link: 'https://attacker.example/' } }],
    ['HTML tooltip mode', { tooltip: { renderMode: 'html' } }],
    ['dataset transform', { dataset: { source: [[1]], transform: { type: 'filter' } } }],
    ['unsupported series', { series: [{ type: 'pie', data: [1] }] }],
    ['custom path symbol', { series: [{ type: 'line', symbol: 'path://M0,0L1,1', data: [1] }] }],
    ['unknown root field', { darkMode: true }],
  ])('rejects %s instead of forwarding it to ECharts', (_, option) => {
    expect(() => parseEChartsOptions(JSON.stringify(option))).toThrow(EChartsValidationError);
  });

  it('accepts twenty thousand local dataset rows without counting series references twice', () => {
    const source = JSON.stringify({
      dataset: { source: Array.from({ length: 20_000 }, (_, index) => [index]) },
      series: [{ type: 'line', datasetIndex: 0 }],
    });

    expect(parseEChartsOptions(source)).toMatchObject({ dataset: { source: expect.any(Array) } });
  });

  it('rejects a dataset with more than twenty thousand local rows', () => {
    const source = JSON.stringify({
      dataset: { source: Array.from({ length: 20_001 }, (_, index) => [index]) },
    });

    expect(() => parseEChartsOptions(source)).toThrow('20,000');
  });

  it('counts distinct local datasets and direct series data together', () => {
    const rows = (count: number) => Array.from({ length: count }, (_, index) => [index]);
    const bounded = JSON.stringify({
      dataset: [{ source: rows(10_000) }, { source: rows(5_000) }],
      series: [{ type: 'line', data: Array.from({ length: 5_000 }, (_, index) => index) }],
    });
    const excessive = JSON.stringify({
      dataset: [{ source: rows(10_000) }, { source: rows(5_000) }],
      series: [{ type: 'line', data: Array.from({ length: 5_001 }, (_, index) => index) }],
    });

    expect(parseEChartsOptions(bounded)).toBeDefined();
    expect(() => parseEChartsOptions(excessive)).toThrow('20,000');
  });
});
