import { beforeEach, describe, expect, it, vi } from 'vitest';

import { authApi } from './auth';
import { request } from './client';

vi.mock('./client', () => ({
  request: vi.fn(),
  setCsrfToken: vi.fn(),
}));

describe('authApi', () => {
  beforeEach(() => vi.clearAllMocks());

  it('logs in with local credentials and returns password state', async () => {
    vi.mocked(request).mockResolvedValue({ status: 'ok', auth_method: 'local', must_change_password: true });

    await authApi.localLogin({ email: 'alice@example.test', password: 'Password123!' });

    expect(request).toHaveBeenCalledWith('/auth/local/login', {
      method: 'POST',
      body: JSON.stringify({ email: 'alice@example.test', password: 'Password123!' }),
    });
  });
});
