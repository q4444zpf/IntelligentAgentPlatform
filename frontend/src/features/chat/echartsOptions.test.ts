import { afterEach, describe, expect, it } from 'vitest';
import { EChartsValidationError, parseEChartsOptions } from './echartsOptions';

function nestedObjects(layers: number): unknown {
  let result: unknown = 0;
  for (let index = 0; index < layers; index += 1) {
    result = { child: result };
  }
  return result;
}

function nestedArrays(layers: number): unknown {
  let result: unknown = 0;
  for (let index = 0; index < layers; index += 1) {
    result = [result];
  }
  return result;
}

function nestedEmptyObjects(containers: number): unknown {
  let result: unknown = {};
  for (let index = 1; index < containers; index += 1) {
    result = { child: result };
  }
  return result;
}

function nestedEmptyArrays(containers: number): unknown {
  let result: unknown = [];
  for (let index = 1; index < containers; index += 1) {
    result = [result];
  }
  return result;
}

afterEach(() => {
  expect((Object.prototype as Record<string, unknown>).polluted).toBeUndefined();
});

describe('parseEChartsOptions', () => {
  it('accepts a bounded JSON object', () => {
    expect(parseEChartsOptions('{"xAxis":{"data":["08:00"]},"series":[{"type":"line","data":[12.3]}]}'))
      .toMatchObject({ series: [{ type: 'line', data: [12.3] }] });
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

  it.each([
    ['objects', nestedObjects(20)],
    ['arrays', { child: nestedArrays(19) }],
  ])('accepts exactly twenty nested %s containers', (_, option) => {
    expect(parseEChartsOptions(JSON.stringify(option))).toBeDefined();
  });

  it.each([
    ['objects', nestedEmptyObjects(21)],
    ['arrays', { child: nestedEmptyArrays(20) }],
  ])('rejects twenty-one nested %s containers', (_, option) => {
    expect(() => parseEChartsOptions(JSON.stringify(option))).toThrow('深度');
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
