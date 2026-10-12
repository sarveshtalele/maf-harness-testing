import { test, expect, request } from '@playwright/test';
import { BASE_URL, SESSION_FILE } from '../playwright.launch';
import { mintReportToken, reportTokenValid } from '../lib/report-token';
import { randomBytes } from 'node:crypto';

// Report files are reached through /api/harness/report/<expiry>.<mac>/runs/<id>/evidence/... by a frame with no cookie.

const ANONYMOUS = { cookies: [], origins: [] };
const KEY = randomBytes(32);
const NOW = Math.floor(Date.now() / 1000);

test('token rules (pure): valid for its run only, before expiry, with the right MAC', () => {
  const token = mintReportToken(KEY, 'run-a', NOW + 60);
  expect(reportTokenValid(token, 'run-a', KEY, NOW * 1000)).toBe(true);
  expect(reportTokenValid(token, 'run-b', KEY, NOW * 1000), 'wrong run').toBe(false);
  expect(reportTokenValid(token, 'run-a', KEY, (NOW + 61) * 1000), 'expired').toBe(false);
  expect(reportTokenValid(mintReportToken(KEY, 'run-a', NOW - 1), 'run-a', KEY, NOW * 1000), 'minted expired').toBe(false);
  expect(reportTokenValid(token, 'run-a', randomBytes(32), NOW * 1000), 'another key').toBe(false);
  expect(reportTokenValid(`${NOW + 60}.${'A'.repeat(43)}`, 'run-a', KEY, NOW * 1000), 'forged').toBe(false);
  expect(reportTokenValid('not-a-token', 'run-a', KEY, NOW * 1000)).toBe(false);
});

test('no token and a forged token get 401 on report files', async () => {
  const api = await request.newContext({ baseURL: BASE_URL, storageState: ANONYMOUS });
  try {
    const file = 'runs/unknown-run/evidence/0123456789abcdef01234567/data.json';
    expect((await api.get(`/api/harness/${file}`)).status(), 'no token').toBe(401);
    expect((await api.get(`/api/harness/report/${NOW + 60}.${'A'.repeat(43)}/${file}`)).status(), 'forged').toBe(401);
    expect((await api.get(`/api/harness/report/${NOW - 60}.${'A'.repeat(43)}/${file}`)).status(), 'expired').toBe(401);
    expect((await api.get(`/api/harness/report/whatever/${file}`)).status(), 'garbage').toBe(401);
  } finally {
    await api.dispose();
  }
});

test('a token for one run is refused on another run, on non-evidence paths, and for non-GET', async () => {
  const session = await request.newContext({ baseURL: BASE_URL, storageState: SESSION_FILE });
  const anon = await request.newContext({ baseURL: BASE_URL, storageState: ANONYMOUS });
  try {
    const minted = await session.get('/api/report-token/run-a');
    expect(minted.status()).toBe(200);
    const { prefix } = (await minted.json()) as { prefix: string };
    expect(prefix).toMatch(/^\/api\/harness\/report\/[0-9]+\.[A-Za-z0-9_-]{43}$/);
    // Wrong run: the token names run-a, the path names run-b.
    expect((await anon.get(`${prefix}/runs/run-b/evidence/0123456789abcdef01234567/data.json`)).status()).toBe(401);
    // Right run, not an evidence file.
    expect((await anon.get(`${prefix}/runs/run-a`)).status()).toBe(401);
    expect((await anon.get(`${prefix}/runs/run-a/logs`)).status()).toBe(401);
    expect((await anon.get(`${prefix}/runs/run-a/prompts`)).status()).toBe(401);
    // Right run and evidence, but not GET.
    expect((await anon.post(`${prefix}/runs/run-a/evidence/0123456789abcdef01234567/data.json`, { data: {} })).status()).toBe(405);
    // A report token is not a session: the normal API still needs the cookie.
    expect((await anon.get(`/api/harness/runs/run-a/evidence`)).status()).toBe(401);
  } finally {
    await session.dispose();
    await anon.dispose();
  }
});

test('a valid token serves that run\'s evidence to a frame with no cookie', async () => {
  const session = await request.newContext({ baseURL: BASE_URL, storageState: SESSION_FILE });
  const anon = await request.newContext({ baseURL: BASE_URL, storageState: ANONYMOUS });
  try {
    // Any run that already has evidence from earlier specs in this run; the test is skipped when none exists yet.
    const runs = (await (await session.get('/api/harness/runs')).json()) as { runs?: { id: string }[] };
    let found: { runId: string; fileId: string } | null = null;
    for (const run of runs.runs ?? []) {
      const listing = await session.get(`/api/harness/runs/${encodeURIComponent(run.id)}/evidence`);
      const files = ((await listing.json()) as { files?: { id: string }[] }).files ?? [];
      if (files[0]) { found = { runId: run.id, fileId: files[0].id }; break; }
    }
    test.skip(!found, 'no run with evidence exists yet in this fixture server');
    const minted = (await (await session.get(`/api/report-token/${encodeURIComponent(found!.runId)}`)).json()) as { prefix: string };
    // No Origin, no cookie: the token alone is the credential, and the frame's own fetch headers do not matter.
    const reply = await anon.get(`${minted.prefix}/runs/${encodeURIComponent(found!.runId)}/evidence/${encodeURIComponent(found!.fileId)}`);
    expect(reply.status()).toBe(200);
  } finally {
    await session.dispose();
    await anon.dispose();
  }
});

test('the token endpoint needs the session and a valid run id', async () => {
  const anon = await request.newContext({ baseURL: BASE_URL, storageState: ANONYMOUS });
  const session = await request.newContext({ baseURL: BASE_URL, storageState: SESSION_FILE });
  try {
    expect((await anon.get('/api/report-token/run-a')).status()).toBe(401);
    expect((await session.get('/api/report-token/..%2Fbad')).status()).toBe(400);
  } finally {
    await anon.dispose();
    await session.dispose();
  }
});
