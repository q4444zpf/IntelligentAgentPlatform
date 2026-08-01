# JSON Request Content-Type Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure JSON requests sent through the frontend API client include `Content-Type: application/json` without overriding explicit media types.

**Architecture:** Keep header normalization inside the shared `request()` boundary so all existing JSON API methods benefit without call-site changes. Infer JSON only for string request bodies and preserve caller-provided headers.

**Tech Stack:** Vue 3, TypeScript, Fetch API, Vitest

---

### Task 1: Add Request Header Regression Coverage

**Files:**
- Create: `frontend/src/api/client.test.ts`
- Test: `frontend/src/api/client.test.ts`

- [ ] **Step 1: Write the failing tests**

```ts
import { afterEach, describe, expect, it, vi } from 'vitest';

import { request } from './client';

afterEach(() => vi.restoreAllMocks());

describe('request headers', () => {
  it('adds JSON content type for a string request body', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}'));

    await request('/models/deepseek/test', { method: 'POST', body: JSON.stringify({ base_url: 'https://api.deepseek.com/v1' }) });

    expect(fetchMock.mock.calls[0]?.[1]?.headers).toMatchObject({ 'Content-Type': 'application/json' });
  });

  it('preserves an explicit content type', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}'));

    await request('/upload', { method: 'POST', body: 'payload', headers: { 'Content-Type': 'text/plain' } });

    expect(fetchMock.mock.calls[0]?.[1]?.headers).toMatchObject({ 'Content-Type': 'text/plain' });
  });

  it('does not add content type when there is no request body', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}'));

    await request('/models');

    expect(fetchMock.mock.calls[0]?.[1]?.headers).not.toHaveProperty('Content-Type');
  });
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `npm test -- --run src/api/client.test.ts`

Expected: the JSON content type test fails because the captured headers do not contain `Content-Type`.

### Task 2: Normalize JSON Request Headers

**Files:**
- Modify: `frontend/src/api/client.ts`
- Test: `frontend/src/api/client.test.ts`

- [ ] **Step 1: Add the minimal header inference**

Inside `request()`, construct headers before `fetch()`:

```ts
const headers = new Headers({ Accept: 'application/json', ...identityHeaders, ...init.headers });
if (typeof init.body === 'string' && !headers.has('Content-Type')) {
  headers.set('Content-Type', 'application/json');
}
```

Pass `headers` to `fetch()` instead of the current object literal.

- [ ] **Step 2: Run the focused test and verify GREEN**

Run: `npm test -- --run src/api/client.test.ts`

Expected: all three request-header tests pass.

- [ ] **Step 3: Run the frontend regression suite**

Run: `npm test -- --run`

Expected: all frontend tests pass.

- [ ] **Step 4: Build the production frontend**

Run: `npm run build`

Expected: TypeScript checking and Vite production build complete successfully; the existing chunk-size warning is acceptable.

### Task 3: Verify the DeepSeek Workflow

**Files:**
- No code changes

- [ ] **Step 1: Rebuild the frontend container**

Run: `docker compose up -d --build web`

Expected: the Web container starts successfully.

- [ ] **Step 2: Verify through the browser**

Open `http://127.0.0.1/llm`, configure DeepSeek, and run both actions. Expected results:

- `PUT /api/models/deepseek/config` no longer returns HTTP 422 and persists the configuration.
- `POST /api/models/deepseek/test` no longer returns HTTP 422 and reaches the upstream connection check.
- Reloading the page shows DeepSeek as configured with a masked API key.

- [ ] **Step 3: Commit the fix**

```bash
git add frontend/src/api/client.ts frontend/src/api/client.test.ts docs/superpowers/plans/2026-08-01-json-content-type-fix.md
git commit -m "fix: send JSON content type from API client"
```
