import { describe, expect, it, vi } from 'vitest';

import { artifactsApi } from './artifacts';

const requestMock = vi.hoisted(() => vi.fn());
vi.mock('./client', () => ({ request: requestMock }));

describe('artifactsApi', () => {
  it('lists visible artifacts from the platform endpoint', async () => {
    requestMock.mockResolvedValueOnce([]);

    await artifactsApi.list();

    expect(requestMock).toHaveBeenCalledWith('/artifacts', { signal: undefined });
  });

  it('returns the presigned download response for an artifact', async () => {
    requestMock.mockResolvedValueOnce({ url: 'http://minio.test/signed', expires_in: 900 });

    const result = await artifactsApi.download('artifact-1');

    expect(requestMock).toHaveBeenCalledWith('/artifacts/artifact-1/download', { signal: undefined });
    expect(result.url).toBe('http://minio.test/signed');
  });
});
