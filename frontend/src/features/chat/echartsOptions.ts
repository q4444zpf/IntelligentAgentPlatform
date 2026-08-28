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

const DANGEROUS_KEYS = new Set(['__proto__', 'constructor', 'prototype']);
const EXTERNAL_RESOURCE = /^(?:https?:|\/\/|data:|javascript:|vbscript:)/i;

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

function isGraphicStyleImage(path: string[], key: string): boolean {
  return key === 'image' && path.at(-1) === 'style' && path.includes('graphic');
}

function walkOption(
  value: unknown,
  depth: number,
  path: string[],
  dataPoints: { total: number },
): void {
  if (!Array.isArray(value) && !isRecord(value)) return;
  const nextDepth = depth + 1;
  if (nextDepth > MAX_ECHARTS_DEPTH) validationError('ECharts 配置嵌套深度超过 20 层限制');

  if (Array.isArray(value)) {
    value.forEach((item) => walkOption(item, nextDepth, path, dataPoints));
    return;
  }

  for (const key of Object.keys(value)) {
    if (DANGEROUS_KEYS.has(key)) validationError('ECharts 配置包含危险字段');
    if (key.toLowerCase().endsWith('formatter')) {
      validationError('ECharts 配置不允许 formatter 字段');
    }
    if (key === 'onclick' && path.at(-1) === 'elements' && path.at(-2) === 'graphic') {
      validationError('ECharts 配置不允许 graphic.elements.onclick');
    }

    const child = value[key];
    if (key === 'series' && path.length === 0) {
      if (!Array.isArray(child)) validationError('ECharts 的 series 必须是数组');
      if (child.length > MAX_ECHARTS_SERIES) validationError('ECharts 的 series 不能超过 20 个');
    }
    if (key === 'data' && path.at(-1) === 'series' && Array.isArray(child)) {
      dataPoints.total += child.length;
      if (dataPoints.total > MAX_ECHARTS_DATA_POINTS) {
        validationError('ECharts 数据点不能超过 20,000 个');
      }
    }
    if (key === 'source' && path.length === 1 && path[0] === 'dataset') {
      if (Array.isArray(child)) {
        dataPoints.total += child.length;
        if (dataPoints.total > MAX_ECHARTS_DATA_POINTS) {
          validationError('ECharts 数据点不能超过 20,000 个');
        }
      }
      if (isExternalResource(child)) validationError('ECharts 配置不允许外部数据源');
    }
    if (key === 'symbol' && isExternalResource(child)) {
      validationError('ECharts 配置不允许外部 image 符号');
    }
    if (isGraphicStyleImage(path, key) && isExternalResource(child)) {
      validationError('ECharts 配置不允许外部资源');
    }
    walkOption(child, nextDepth, [...path, key], dataPoints);
  }
}

export function parseEChartsOptions(source: string): Record<string, unknown> {
  if (new TextEncoder().encode(source).byteLength > MAX_ECHARTS_BYTES) {
    validationError('ECharts 源码超过 512 KB 限制');
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(source);
  } catch {
    validationError('ECharts 源码必须是有效 JSON');
  }

  if (!isRecord(parsed)) validationError('ECharts 配置根节点必须是对象');
  walkOption(parsed, 0, [], { total: 0 });
  return parsed;
}
