import { test, expect, request } from '@playwright/test';
import { BASE_URL, LAUNCH_SECRET, SESSION_FILE } from '../playwright.launch';

// The config's storageState would otherwise reach these clients too: they must start with no cookie at all.
const ANONYMOUS = { cookies: [], origins: [] };

// F1: every API call and page needs the launch session. F22: pages send a nonce CSP without unsafe-inline scripts.

test('a curl-style request with no Origin and no cookie gets 401', async () => {
  const api = await request.newContext({ baseURL: BASE_URL, storageState: ANONYMOUS });
  try {
    const response = await api.get('/api/harness/config');
    expect(response.status()).toBe(401);
    expect(await response.text()).toContain('start-windows.cmd');
    const runs = await api.post('/api/harness/runs/anything/respond', { data: { decision: 'approve' } });
    expect(runs.status()).toBe(401);
  } finally {
    await api.dispose();
  }
});

test('the bare address without a session shows the 401 page with launcher instructions', async () => {
  const api = await request.newContext({ baseURL: BASE_URL, storageState: ANONYMOUS });
  try {
    const response = await api.get('/runs');
    expect(response.status()).toBe(401);
    expect(response.headers()['content-type']).toContain('text/html');
    const body = await response.text();
    expect(body).toContain('Open the app from the launcher');
    expect(body).toContain('start-macos.command');
    expect(response.headers()['content-security-policy']).toContain("script-src 'self' 'nonce-");
  } finally {
    await api.dispose();
  }
});

test('the launch secret works once: a second exchange fails', async () => {
  // The global setup already spent the secret for the saved session.
  const api = await request.newContext({ baseURL: BASE_URL, storageState: ANONYMOUS });
  try {
    const again = await api.get(`/api/session?launch=${LAUNCH_SECRET}`, { maxRedirects: 0 });
    expect(again.status()).toBe(401);
    expect(again.headers()['set-cookie'] ?? '').not.toContain('aqe_session=');
    const wrong = await api.get('/api/session?launch=not-the-secret', { maxRedirects: 0 });
    expect(wrong.status()).toBe(401);
  } finally {
    await api.dispose();
  }
});

test('a session cookie reaches the proxy, and cross-site requests are refused', async () => {
  const api = await request.newContext({ baseURL: BASE_URL, storageState: SESSION_FILE });
  try {
    const config = await api.get('/api/harness/config');
    expect(config.status()).toBe(200);
    expect(await config.json()).toEqual(expect.objectContaining({ stages: expect.any(Array) }));
    const crossSite = await api.get('/api/harness/config', { headers: { 'Sec-Fetch-Site': 'cross-site' } });
    expect(crossSite.status()).toBe(403);
    const crossPage = await api.get('/runs', { headers: { 'Sec-Fetch-Site': 'cross-site' } });
    expect(crossPage.status()).toBe(401);
  } finally {
    await api.dispose();
  }
});

test('report files: only a sandboxed report frame (opaque origin, cors/empty fetch) reads them without a session', async () => {
  const api = await request.newContext({ baseURL: BASE_URL, storageState: ANONYMOUS });
  const file = '/api/harness/runs/unknown-run/evidence/0123456789abcdef01234567/data.json';
  try {
    // Opaque origin without the frame's fetch metadata, and an ordinary request, both need the session.
    expect((await api.get(file, { headers: { Origin: 'null' } })).status()).toBe(403);
    expect((await api.get(file)).status()).toBe(401);
    // What a sandboxed report frame sends: Origin null, cors, empty destination. It passes the session check; the
    // unknown run answers 404 from the backend.
    const frame = await api.get(file, { headers: { Origin: 'null', 'Sec-Fetch-Mode': 'cors', 'Sec-Fetch-Dest': 'empty', 'Sec-Fetch-Site': 'cross-site' } });
    expect([401, 403]).not.toContain(frame.status());
  } finally {
    await api.dispose();
  }
});

test('pages send a nonce CSP and no unsafe-inline script policy', async () => {
  const api = await request.newContext({ baseURL: BASE_URL, storageState: SESSION_FILE });
  try {
    const page = await api.get('/runs');
    expect(page.status()).toBe(200);
    const csp = page.headers()['content-security-policy'] ?? '';
    expect(csp).toMatch(/script-src 'self' 'nonce-[A-Za-z0-9+/=]+' 'strict-dynamic'/);
    const scriptSrc = csp.split(';').map((d) => d.trim()).find((d) => d.startsWith('script-src')) ?? '';
    expect(scriptSrc).not.toContain("'unsafe-inline'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("frame-ancestors 'self'");
    expect(page.headers()['x-frame-options']).toBe('SAMEORIGIN');
  } finally {
    await api.dispose();
  }
});

test('the UI hydrates under the nonce CSP without violations', async ({ page }) => {
  const violations: string[] = [];
  page.on('console', (message) => {
    if (/Content Security Policy|Refused to (execute|load)/.test(message.text())) violations.push(message.text());
  });
  page.on('pageerror', (error) => violations.push(error.message));
  await page.goto('/runs');
  await expect(page.locator('html')).toHaveAttribute('data-theme', /^(light|dark)$/);
  await expect(page.locator('#main-content').first()).toBeVisible();
  expect(violations).toEqual([]);
});

test('a manual visit in a fresh browser context shows the sign-in page', async ({ browser }) => {
  const context = await browser.newContext({ baseURL: BASE_URL, storageState: ANONYMOUS });
  const page = await context.newPage();
  try {
    const response = await page.goto('/runs');
    expect(response?.status()).toBe(401);
    await expect(page.locator('h1')).toHaveText('401: Open the app from the launcher');
  } finally {
    await context.close();
  }
});
