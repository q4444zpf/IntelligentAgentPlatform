import { afterEach, describe, expect, it } from 'vitest';
import { EChartsValidationError, parseEChartsOptions } from './echartsOptions';

function nestedObject(depth: number): Record<string, unknown> {
  let result: Record<string, unknown> = {};
  for (let index = 0; index < depth; index += 1) {
    result = { child: result };
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

  it('rejects nesting deeper than twenty levels', () => {
    expect(() => parseEChartsOptions(JSON.stringify(nestedObject(21)))).toThrow('深度');
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
  ])('rejects prohibited ECharts content %s', (source, message) => {
    expect(() => parseEChartsOptions(source)).toThrow(message);
  });
});
