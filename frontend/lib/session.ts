// Server-only: browser sessions for the local UI. Import from route handlers and proxy.ts only.
//
// State lives in <HARNESS_UI_STATE_DIR>/ui-session-state.json (0600): a signing key and the digest of the launch secret
// already spent. Both survive a Next restart, so a restart neither replays a used link nor logs the user out.
// A session cookie is "<expiry>.<id>.<mac>" with mac = HMAC(key, port|expiry|id): nothing per session is stored.
import { createHash, createHmac, randomBytes, timingSafeEqual } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

/** Session cookie lifetime: a week, so a reopened browser still works. */
export const SESSION_TTL_SECONDS = 7 * 24 * 3600;
/** Env var holding the launch secret; the launcher, the desktop shell and Playwright set it per run. */
export const LAUNCH_SECRET_ENV = 'HARNESS_LAUNCH_SECRET';
/** Env var naming the 0600 state directory. Unset means a memory-only fallback: sessions end with the process. */
export const STATE_DIR_ENV = 'HARNESS_UI_STATE_DIR';

interface State {
  key: Buffer;
  launchUsed: string | null; // sha256 hex of the launch secret spent by /api/session
  persistent: boolean;
  file: string | null;
}

const STATE_KEY = '__agenticQeSessionState';
const holder = globalThis as unknown as Record<string, State | undefined>;

function persist(state: State): void {
  if (!state.file) return;
  fs.mkdirSync(path.dirname(state.file), { recursive: true, mode: 0o700 });
  const tmp = `${state.file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify({ key: state.key.toString('base64'), launchUsed: state.launchUsed }), { mode: 0o600 });
  fs.renameSync(tmp, state.file);
  try { fs.chmodSync(state.file, 0o600); } catch { /* Windows */ }
}

function state(): State {
  if (holder[STATE_KEY]) return holder[STATE_KEY];
  const dir = process.env[STATE_DIR_ENV] ?? '';
  const file = dir ? path.join(dir, 'ui-session-state.json') : null;
  let stored: { key?: unknown; launchUsed?: unknown } = {};
  if (file && fs.existsSync(file)) {
    try { stored = JSON.parse(fs.readFileSync(file, 'utf8')); } catch { /* unreadable: start a new key */ }
  }
  const restored = typeof stored.key === 'string' ? Buffer.from(stored.key, 'base64') : null;
  const next: State = {
    key: restored && restored.length === 32 ? restored : randomBytes(32),
    launchUsed: typeof stored.launchUsed === 'string' ? stored.launchUsed : null,
    persistent: !!file,
    file,
  };
  holder[STATE_KEY] = next;
  if (file) persist(next);
  return next;
}

/** Tests only: forget the in-process copy, as a restart would, so the next call reads the file again. */
export function resetSessionStateForTests(): void {
  delete holder[STATE_KEY];
}

/** The per-install signing key (also used for report tokens). */
export function signingKey(): Buffer {
  return state().key;
}

/** True when the state is kept in a file, so sessions and the spent launch secret survive a restart. */
export function sessionsSurviveRestart(): boolean {
  return state().persistent;
}

function digest(value: string): string {
  return createHash('sha256').update(value).digest('hex');
}

export function mac(key: Buffer, parts: Array<string | number>): string {
  return createHmac('sha256', key).update(parts.join('|')).digest('base64url');
}

/** Constant-time string comparison that tolerates different lengths. */
export function safeEqual(a: string, b: string): boolean {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

/** Cookie name per UI port, so another local app on another port cannot clobber it. */
export function sessionCookieName(port: string): string {
  return `aqe_session_${port}`;
}

/** The port of the Host header (the UI's own port), or '' when absent. */
export function portOf(request: Request): string {
  return (request.headers.get('host') ?? '').match(/:(\d{1,5})$/)?.[1] ?? '';
}

/**
 * Exchanges the one-time launch secret for a session cookie value. Single use and persisted: a second exchange, a restart,
 * or a wrong secret returns null. `now` is injectable for tests.
 */
export function exchangeLaunchSecret(secret: string, port: string, now = Date.now()): string | null {
  const envSecret = process.env[LAUNCH_SECRET_ENV] ?? '';
  if (!envSecret || !secret || !port) return null;
  const s = state();
  const presented = digest(secret);
  if (!safeEqual(presented, digest(envSecret))) return null;
  if (s.launchUsed === presented) return null;
  s.launchUsed = presented;
  persist(s); // written before the cookie is returned, so a crash cannot replay the link
  return issueSession(port, now);
}

/** A signed session cookie value valid for SESSION_TTL_SECONDS. */
export function issueSession(port: string, now = Date.now()): string {
  const expiry = Math.floor(now / 1000) + SESSION_TTL_SECONDS;
  const id = randomBytes(24).toString('base64url');
  return `${expiry}.${id}.${mac(state().key, ['session', port, expiry, id])}`;
}

/** True when `value` is a session issued for this port that has not expired. */
export function validSession(value: string, port: string, now = Date.now()): boolean {
  const m = /^(\d{10,})\.([A-Za-z0-9_-]{32})\.([A-Za-z0-9_-]{43})$/.exec(value);
  if (!m || !port) return false;
  const expiry = Number(m[1]);
  if (expiry <= Math.floor(now / 1000)) return false;
  return safeEqual(m[3], mac(state().key, ['session', port, expiry, m[2]]));
}

/** True when the request carries a valid session cookie for its own port. */
export function hasSession(request: Request): boolean {
  const port = portOf(request);
  const name = sessionCookieName(port);
  const cookie = request.headers.get('cookie') ?? '';
  for (const part of cookie.split(';')) {
    const eq = part.indexOf('=');
    if (eq > 0 && part.slice(0, eq).trim() === name) return validSession(part.slice(eq + 1).trim(), port);
  }
  return false;
}

/** Host header allowlist (DNS rebinding): only loopback names on any port. */
export function hostAllowed(request: Request): boolean {
  return /^(localhost|127\.0\.0\.1):\d+$/.test(request.headers.get('host') ?? '');
}

/** Cross-site requests are never allowed: a page on another site cannot drive the local API or the UI. */
export function isCrossSite(request: Request): boolean {
  return request.headers.get('sec-fetch-site') === 'cross-site';
}

/** Set-Cookie value for a session. HttpOnly and SameSite=Strict; persistent for SESSION_TTL_SECONDS. */
export function sessionSetCookie(port: string, value: string): string {
  return `${sessionCookieName(port)}=${value}; Path=/; Max-Age=${SESSION_TTL_SECONDS}; HttpOnly; SameSite=Strict`;
}

/** The 401 page: what to do when the UI is opened by hand instead of from the launcher or the desktop app. */
export function unauthorizedPage(): string {
  const build = (process.env.HARNESS_BUILD_ID ?? '').replace(/[^A-Za-z0-9._-]/g, '');
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">`
    + `<meta name="agentic-qe-build" content="${build}"><title>Agentic QE Platform: sign-in required</title>`
    + `<style>body{font-family:system-ui,sans-serif;margin:0;min-height:100vh;display:grid;place-items:center;background:#fff;color:#1f2933}`
    + `main{max-width:560px;padding:16px;line-height:1.5}code{background:#f1f3f5;padding:2px 4px;border-radius:4px}</style></head>`
    + `<body><main><h1>401: Open the app from the launcher</h1>`
    + `<p>The Agentic QE Platform only opens for the window that its launcher starts. This address was opened directly, so no session exists.</p>`
    + `<p>Open the app from the launcher (<code>start-windows.cmd</code> / <code>start-macos.command</code> / <code>start-linux.sh</code>), `
    + `or use the link it printed. The link works once. Your browser keeps the session for 7 days; to get a new link, stop the running launcher (Ctrl+C in its terminal) and start it again.</p></main></body></html>`;
}
