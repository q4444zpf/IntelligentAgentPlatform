import { MAX_ECHARTS_BYTES } from './answerBlocks';

export class EChartsValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'EChartsValidationError';
  }
}

export const MAX_ECHARTS_DEPTH = 20;
export const MAX_ECHARTS_SERIES = 20;
export const MAX_ECHARTS_DATA_POINTS = 20_000;

type Copier = (value: unknown, path: string) => unknown;
type JsonPrimitive = boolean | null | number | string;

const DANGEROUS_KEYS = new Set(['__proto__', 'constructor', 'prototype']);
const EXTERNAL_RESOURCE = /^(?:https?:|\/\/|data:|javascript:|vbscript:)/i;
const UNSAFE_COLOR = /(?:url\s*\(|image:\/\/|path:\/\/|@import|expression\s*\()/i;
const SERIES_TYPES = new Set(['line', 'bar', 'scatter']);
const SYMBOLS = new Set(['circle', 'rect', 'roundRect', 'triangle', 'diamond', 'pin', 'arrow', 'none']);

function validationError(message: string): never {
  throw new EChartsValidationError(message);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isExternalResource(value: unknown): boolean {
  if (typeof value !== 'string') return false;
  let normalized = value.trim();
  if (normalized.toLowerCase().startsWith('image://')) {
    normalized = normalized.slice('image://'.length).trim();
  }
  return EXTERNAL_RESOURCE.test(normalized);
}

function validateJsonDepth(source: string): void {
  let depth = 0;
  let escaped = false;
  let inString = false;

  for (const character of source) {
    if (inString) {
      if (escaped) escaped = false;
      else if (character === '\\') escaped = true;
      else if (character === '"') inString = false;
      continue;
    }
    if (character === '"') {
      inString = true;
      continue;
    }
    if (character === '{' || character === '[') {
      depth += 1;
      if (depth > MAX_ECHARTS_DEPTH) validationError('ECharts 配置嵌套深度超过 20 层限制');
    } else if (character === '}' || character === ']') {
      depth -= 1;
    }
  }
}

function scanStructure(root: unknown): void {
  const stack: Array<{ path: string[]; value: unknown }> = [{ path: [], value: root }];

  while (stack.length) {
    const current = stack.pop()!;
    if (Array.isArray(current.value)) {
      for (const child of current.value) stack.push({ path: current.path, value: child });
      continue;
    }
    if (!isRecord(current.value)) continue;

    for (const key of Object.keys(current.value)) {
      if (DANGEROUS_KEYS.has(key)) validationError('ECharts 配置包含危险字段');
      if (key.toLowerCase().endsWith('formatter')) validationError('ECharts 配置不允许 formatter 字段');
      if (key === 'onclick' && current.path.at(-1) === 'elements' && current.path.at(-2) === 'graphic') {
        validationError('ECharts 配置不允许 graphic.elements.onclick');
      }

      const child = current.value[key];
      if (key === 'source' && current.path.length === 1 && current.path[0] === 'dataset'
        && isExternalResource(child)) {
        validationError('ECharts 配置不允许外部数据源');
      }
      if (key === 'symbol' && isExternalResource(child)) {
        validationError('ECharts 配置不允许外部 image 符号');
      }
      if (key === 'image' && current.path.at(-1) === 'style' && current.path.includes('graphic')
        && isExternalResource(child)) {
        validationError('ECharts 配置不允许外部资源');
      }
      stack.push({ path: [...current.path, key], value: child });
    }
  }
}

function copyObject(value: unknown, fields: Record<string, Copier>, path: string): Record<string, unknown> {
  if (!isRecord(value)) validationError(`${path} 必须是对象`);
  const result: Record<string, unknown> = {};
  for (const key of Object.keys(value)) {
    if (!Object.hasOwn(fields, key)) validationError(`${path} 不支持字段 ${key}`);
    result[key] = fields[key](value[key], `${path}.${key}`);
  }
  return result;
}

function copyArray(value: unknown, path: string, copier: Copier): unknown[] {
  if (!Array.isArray(value)) validationError(`${path} 必须是数组`);
  return value.map((item, index) => copier(item, `${path}[${index}]`));
}

function copyBoolean(value: unknown, path: string): boolean {
  if (typeof value !== 'boolean') validationError(`${path} 必须是布尔值`);
  return value;
}

function copyNumber(value: unknown, path: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) validationError(`${path} 必须是有限数字`);
  return value;
}

function copyString(value: unknown, path: string): string {
  if (typeof value !== 'string') validationError(`${path} 必须是字符串`);
  return value;
}

function copyStringOrNumber(value: unknown, path: string): number | string {
  if (typeof value === 'string') return value;
  return copyNumber(value, path);
}

function copyPrimitive(value: unknown, path: string): JsonPrimitive {
  if (value === null || typeof value === 'boolean' || typeof value === 'string') return value;
  return copyNumber(value, path);
}

function copyEnum(allowed: Set<string>): Copier {
  return (value, path) => {
    const result = copyString(value, path);
    if (!allowed.has(result)) validationError(`${path} 包含不支持的值`);
    return result;
  };
}

function copyColor(value: unknown, path: string): string {
  const result = copyString(value, path).trim();
  if (!result || result.length > 128 || UNSAFE_COLOR.test(result) || isExternalResource(result)) {
    validationError(`${path} 包含不安全的颜色值`);
  }
  return result;
}

function copyPadding(value: unknown, path: string): number | number[] {
  if (typeof value === 'number') return copyNumber(value, path);
  const result = copyArray(value, path, copyNumber) as number[];
  if (result.length < 1 || result.length > 4) validationError(`${path} 只能包含 1 到 4 个数字`);
  return result;
}

function cloneDatasetValue(value: unknown, path: string): unknown {
  if (Array.isArray(value)) return value.map((item, index) => cloneDatasetValue(item, `${path}[${index}]`));
  if (!isRecord(value)) return copyPrimitive(value, path);
  const result: Record<string, unknown> = {};
  for (const key of Object.keys(value)) {
    Object.defineProperty(result, key, {
      configurable: true,
      enumerable: true,
      value: cloneDatasetValue(value[key], `${path}.${key}`),
      writable: true,
    });
  }
  return result;
}

const position: Copier = copyStringOrNumber;
const stringArray: Copier = (value, path) => copyArray(value, path, copyString);
const dimensionArray: Copier = (value, path) => copyArray(value, path, copyString);

const textStyleFields: Record<string, Copier> = {
  align: copyString,
  color: copyColor,
  fontFamily: copyString,
  fontSize: copyNumber,
  fontStyle: copyEnum(new Set(['normal', 'italic', 'oblique'])),
  fontWeight: copyStringOrNumber,
  lineHeight: copyNumber,
  verticalAlign: copyString,
};

const lineStyleFields: Record<string, Copier> = {
  color: copyColor,
  opacity: copyNumber,
  type: copyEnum(new Set(['solid', 'dashed', 'dotted'])),
  width: copyNumber,
};

const itemStyleFields: Record<string, Copier> = {
  borderColor: copyColor,
  borderType: copyEnum(new Set(['solid', 'dashed', 'dotted'])),
  borderWidth: copyNumber,
  color: copyColor,
  opacity: copyNumber,
};

const labelFields: Record<string, Copier> = {
  color: copyColor,
  distance: copyNumber,
  fontSize: copyNumber,
  fontWeight: copyStringOrNumber,
  position: copyString,
  rotate: copyNumber,
  show: copyBoolean,
};

const areaStyleFields: Record<string, Copier> = {
  color: copyColor,
  opacity: copyNumber,
};

const titleFields: Record<string, Copier> = {
  bottom: position,
  itemGap: copyNumber,
  left: position,
  padding: copyPadding,
  right: position,
  subtext: copyString,
  subtextStyle: (value, path) => copyObject(value, textStyleFields, path),
  text: copyString,
  textAlign: copyString,
  textStyle: (value, path) => copyObject(value, textStyleFields, path),
  textVerticalAlign: copyString,
  top: position,
};

function copySelected(value: unknown, path: string): Record<string, boolean> {
  if (!isRecord(value)) validationError(`${path} 必须是对象`);
  const result: Record<string, boolean> = {};
  for (const key of Object.keys(value)) {
    Object.defineProperty(result, key, {
      configurable: true,
      enumerable: true,
      value: copyBoolean(value[key], `${path}.${key}`),
      writable: true,
    });
  }
  return result;
}

const legendFields: Record<string, Copier> = {
  bottom: position,
  data: stringArray,
  itemGap: copyNumber,
  itemHeight: copyNumber,
  itemWidth: copyNumber,
  left: position,
  orient: copyEnum(new Set(['horizontal', 'vertical'])),
  padding: copyPadding,
  right: position,
  selected: copySelected,
  show: copyBoolean,
  textStyle: (value, path) => copyObject(value, textStyleFields, path),
  top: position,
  type: copyEnum(new Set(['plain', 'scroll'])),
};

const axisPointerFields: Record<string, Copier> = {
  label: (value, path) => copyObject(value, labelFields, path),
  lineStyle: (value, path) => copyObject(value, lineStyleFields, path),
  shadowStyle: (value, path) => copyObject(value, areaStyleFields, path),
  type: copyEnum(new Set(['line', 'shadow', 'cross', 'none'])),
};

const tooltipFields: Record<string, Copier> = {
  axisPointer: (value, path) => copyObject(value, axisPointerFields, path),
  confine: copyBoolean,
  renderMode: (value, path) => {
    if (value !== 'richText') validationError(`${path} 只允许 richText`);
    return 'richText';
  },
  show: copyBoolean,
  trigger: copyEnum(new Set(['axis', 'item', 'none'])),
};

const gridFields: Record<string, Copier> = {
  backgroundColor: copyColor,
  borderColor: copyColor,
  borderWidth: copyNumber,
  bottom: position,
  containLabel: copyBoolean,
  height: position,
  left: position,
  right: position,
  show: copyBoolean,
  top: position,
  width: position,
};

const axisLineFields: Record<string, Copier> = {
  lineStyle: (value, path) => copyObject(value, lineStyleFields, path),
  onZero: copyBoolean,
  show: copyBoolean,
};

const axisTickFields: Record<string, Copier> = {
  alignWithLabel: copyBoolean,
  inside: copyBoolean,
  length: copyNumber,
  lineStyle: (value, path) => copyObject(value, lineStyleFields, path),
  show: copyBoolean,
};

const axisLabelFields: Record<string, Copier> = {
  color: copyColor,
  fontSize: copyNumber,
  fontWeight: copyStringOrNumber,
  hideOverlap: copyBoolean,
  inside: copyBoolean,
  interval: copyStringOrNumber,
  margin: copyNumber,
  rotate: copyNumber,
  show: copyBoolean,
};

const splitLineFields: Record<string, Copier> = {
  lineStyle: (value, path) => copyObject(value, lineStyleFields, path),
  show: copyBoolean,
};

const axisFields: Record<string, Copier> = {
  axisLabel: (value, path) => copyObject(value, axisLabelFields, path),
  axisLine: (value, path) => copyObject(value, axisLineFields, path),
  axisTick: (value, path) => copyObject(value, axisTickFields, path),
  boundaryGap: (value, path) => Array.isArray(value)
    ? copyArray(value, path, copyStringOrNumber)
    : copyBoolean(value, path),
  data: (value, path) => copyArray(value, path, copyPrimitive),
  inverse: copyBoolean,
  max: copyStringOrNumber,
  min: copyStringOrNumber,
  name: copyString,
  nameGap: copyNumber,
  nameLocation: copyEnum(new Set(['start', 'middle', 'center', 'end'])),
  offset: copyNumber,
  position: copyEnum(new Set(['top', 'bottom', 'left', 'right'])),
  show: copyBoolean,
  splitLine: (value, path) => copyObject(value, splitLineFields, path),
  type: copyEnum(new Set(['value', 'category', 'time', 'log'])),
};

function copyObjectOrArray(value: unknown, path: string, fields: Record<string, Copier>): unknown {
  return Array.isArray(value)
    ? value.map((item, index) => copyObject(item, fields, `${path}[${index}]`))
    : copyObject(value, fields, path);
}

const datasetFields: Record<string, Copier> = {
  dimensions: dimensionArray,
  seriesLayoutBy: copyEnum(new Set(['column', 'row'])),
  source: (value, path) => copyArray(value, path, cloneDatasetValue),
  sourceHeader: (value, path) => value === 'auto' ? value : copyBoolean(value, path),
};

function copyEncode(value: unknown, path: string): Record<string, unknown> {
  if (!isRecord(value)) validationError(`${path} 必须是对象`);
  const result: Record<string, unknown> = {};
  for (const key of Object.keys(value)) {
    const item = value[key];
    const copied = Array.isArray(item)
      ? copyArray(item, `${path}.${key}`, copyStringOrNumber)
      : copyStringOrNumber(item, `${path}.${key}`);
    Object.defineProperty(result, key, {
      configurable: true,
      enumerable: true,
      value: copied,
      writable: true,
    });
  }
  return result;
}

function copySymbol(value: unknown, path: string): string {
  const result = copyString(value, path).trim();
  if (result.toLowerCase().startsWith('image://')) validationError('ECharts 配置不允许外部 image 符号');
  if (result.toLowerCase().startsWith('path://') || !SYMBOLS.has(result)) {
    validationError(`${path} 包含不支持的 symbol`);
  }
  return result;
}

function copySymbolSize(value: unknown, path: string): number | number[] {
  if (typeof value === 'number') return copyNumber(value, path);
  const result = copyArray(value, path, copyNumber) as number[];
  if (result.length !== 2) validationError(`${path} 数组必须包含两个数字`);
  return result;
}

function copySeriesDataItem(value: unknown, path: string): unknown {
  if (Array.isArray(value)) return value.map((item, index) => copyPrimitive(item, `${path}[${index}]`));
  if (!isRecord(value)) return copyPrimitive(value, path);
  return copyObject(value, {
    itemStyle: (item, itemPath) => copyObject(item, itemStyleFields, itemPath),
    label: (item, itemPath) => copyObject(item, labelFields, itemPath),
    name: copyString,
    symbol: copySymbol,
    symbolSize: copySymbolSize,
    value: (item, itemPath) => Array.isArray(item)
      ? copyArray(item, itemPath, copyPrimitive)
      : copyPrimitive(item, itemPath),
  }, path);
}

const commonSeriesFields: Record<string, Copier> = {
  clip: copyBoolean,
  data: (value, path) => copyArray(value, path, copySeriesDataItem),
  datasetIndex: copyNumber,
  dimensions: dimensionArray,
  encode: copyEncode,
  itemStyle: (value, path) => copyObject(value, itemStyleFields, path),
  label: (value, path) => copyObject(value, labelFields, path),
  name: copyString,
  seriesLayoutBy: copyEnum(new Set(['column', 'row'])),
  stack: copyString,
  type: copyString,
  xAxisIndex: copyNumber,
  yAxisIndex: copyNumber,
};

const lineSeriesFields: Record<string, Copier> = {
  ...commonSeriesFields,
  areaStyle: (value, path) => copyObject(value, areaStyleFields, path),
  connectNulls: copyBoolean,
  lineStyle: (value, path) => copyObject(value, lineStyleFields, path),
  sampling: copyEnum(new Set(['lttb', 'average', 'max', 'min', 'sum'])),
  showSymbol: copyBoolean,
  smooth: copyBoolean,
  step: (value, path) => typeof value === 'boolean'
    ? value
    : copyEnum(new Set(['start', 'middle', 'end']))(value, path),
  symbol: copySymbol,
  symbolSize: copySymbolSize,
};

const barSeriesFields: Record<string, Copier> = {
  ...commonSeriesFields,
  backgroundStyle: (value, path) => copyObject(value, itemStyleFields, path),
  barCategoryGap: position,
  barGap: position,
  barMaxWidth: position,
  barMinWidth: position,
  barWidth: position,
  roundCap: copyBoolean,
  showBackground: copyBoolean,
};

const scatterSeriesFields: Record<string, Copier> = {
  ...commonSeriesFields,
  large: copyBoolean,
  largeThreshold: copyNumber,
  symbol: copySymbol,
  symbolSize: copySymbolSize,
};

function copySeries(value: unknown, path: string, dataPoints: { total: number }): Record<string, unknown> {
  if (!isRecord(value)) validationError(`${path} 必须是对象`);
  if (Array.isArray(value.data)) {
    dataPoints.total += value.data.length;
    if (dataPoints.total > MAX_ECHARTS_DATA_POINTS) {
      validationError('ECharts 数据点不能超过 20,000 个');
    }
  }
  const type = value.type;
  if (typeof type !== 'string' || !SERIES_TYPES.has(type)) validationError(`${path}.type 只支持 line、bar 或 scatter`);
  const fields = type === 'line' ? lineSeriesFields : type === 'bar' ? barSeriesFields : scatterSeriesFields;
  return copyObject(value, fields, path);
}

export function parseEChartsOptions(source: string): Record<string, unknown> {
  if (new TextEncoder().encode(source).byteLength > MAX_ECHARTS_BYTES) {
    validationError('ECharts 源码超过 512 KB 限制');
  }
  validateJsonDepth(source);

  let parsed: unknown;
  try {
    parsed = JSON.parse(source);
  } catch {
    validationError('ECharts 源码必须是有效 JSON');
  }
  if (!isRecord(parsed)) validationError('ECharts 配置根节点必须是对象');
  scanStructure(parsed);

  const dataPoints = { total: 0 };
  const rootFields: Record<string, Copier> = {
    dataset: (value, path) => {
      const datasets = Array.isArray(value) ? value : [value];
      for (const dataset of datasets) {
        if (isRecord(dataset) && Array.isArray(dataset.source)) {
          dataPoints.total += dataset.source.length;
          if (dataPoints.total > MAX_ECHARTS_DATA_POINTS) {
            validationError('ECharts 数据点不能超过 20,000 个');
          }
        }
      }
      return copyObjectOrArray(value, path, datasetFields);
    },
    grid: (value, path) => copyObjectOrArray(value, path, gridFields),
    legend: (value, path) => copyObject(value, legendFields, path),
    series: (value, path) => {
      if (!Array.isArray(value)) validationError('ECharts 的 series 必须是数组');
      if (value.length > MAX_ECHARTS_SERIES) validationError('ECharts 的 series 不能超过 20 个');
      return value.map((item, index) => copySeries(item, `${path}[${index}]`, dataPoints));
    },
    title: (value, path) => copyObject(value, titleFields, path),
    tooltip: (value, path) => copyObject(value, tooltipFields, path),
    xAxis: (value, path) => copyObjectOrArray(value, path, axisFields),
    yAxis: (value, path) => copyObjectOrArray(value, path, axisFields),
  };

  const result = copyObject(parsed, rootFields, 'ECharts 配置');
  if (isRecord(result.tooltip)) result.tooltip.renderMode = 'richText';
  result.animation = false;
  return result;
}
