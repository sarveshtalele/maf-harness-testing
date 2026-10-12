import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test, expect } from '@playwright/test';

// Restart behaviour of the UI session store. Runs the real lib/session.ts against a private state directory:
// a "restart" is forgetting the in-process copy, exactly what a new Next server process starts with.

const SECRET = 'restart-check-launch-secret-0123456789abcdef';

test('a restart keeps valid sessions, never replays the launch secret, and keeps the state file private', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'maf-state-check-'));
  process.env.HARNESS_UI_STATE_DIR = dir;
  process.env.HARNESS_LAUNCH_SECRET = SECRET;
  const session = await import('../lib/session');
  try {
    session.resetSessionStateForTests();
    expect(session.sessionsSurviveRestart()).toBe(true);
    const cookie = session.exchangeLaunchSecret(SECRET, '4444');
    expect(cookie, 'first exchange succeeds').toBeTruthy();
    expect(session.validSession(cookie!, '4444')).toBe(true);
    expect(session.exchangeLaunchSecret(SECRET, '4444'), 'second exchange in this process fails').toBeNull();

    session.resetSessionStateForTests(); // restart
    expect(session.exchangeLaunchSecret(SECRET, '4444'), 'replay after restart fails').toBeNull();
    expect(session.validSession(cookie!, '4444'), 'session survives the restart').toBe(true);
    expect(session.validSession(cookie!, '5555'), 'a session is bound to its port').toBe(false);

    const file = path.join(dir, 'ui-session-state.json');
    expect(JSON.parse(fs.readFileSync(file, 'utf8')).key).toBeTruthy();
    if (process.platform !== 'win32') expect(fs.statSync(file).mode & 0o777).toBe(0o600);
    expect(fs.readFileSync(file, 'utf8')).not.toContain(SECRET); // only the digest of the spent secret is stored
  } finally {
    session.resetSessionStateForTests();
    delete process.env.HARNESS_UI_STATE_DIR;
    delete process.env.HARNESS_LAUNCH_SECRET;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('expired and tampered session cookies are refused', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'maf-state-expiry-'));
  process.env.HARNESS_UI_STATE_DIR = dir;
  process.env.HARNESS_LAUNCH_SECRET = SECRET;
  const session = await import('../lib/session');
  try {
    session.resetSessionStateForTests();
    const now = Date.now();
    const cookie = session.issueSession('4444', now);
    expect(session.validSession(cookie, '4444', now + 1000)).toBe(true);
    expect(session.validSession(cookie, '4444', now + 8 * 24 * 3600 * 1000), 'after the week').toBe(false);
    const [expiry, id, mac] = cookie.split('.');
    expect(session.validSession(`${Number(expiry) + 99999}.${id}.${mac}`, '4444', now), 'expiry is signed').toBe(false);
    expect(session.validSession(`${expiry}.${id}.${'A'.repeat(43)}`, '4444', now), 'forged mac').toBe(false);
  } finally {
    session.resetSessionStateForTests();
    delete process.env.HARNESS_UI_STATE_DIR;
    delete process.env.HARNESS_LAUNCH_SECRET;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
