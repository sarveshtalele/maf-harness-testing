// Pure helpers: no Electron imports so they can be unit-tested with node:test.
import { execFile } from 'node:child_process';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import * as net from 'node:net';

/** Ask the OS for a free loopback port (listen on 0, read it, close). */
export function pickFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const addr = srv.address();
      const port = typeof addr === 'object' && addr ? addr.port : 0;
      srv.close(() => (port ? resolve(port) : reject(new Error('no port'))));
    });
  });
}

/** Two distinct free ports (the OS may hand the same one back after close). */
export async function pickTwoPorts(): Promise<[number, number]> {
  const a = await pickFreePort();
  let b = await pickFreePort();
  while (b === a) b = await pickFreePort();
  return [a, b];
}

/** 48 random bytes, url-safe (64 chars); the backend requires >= 32. */
export function makeToken(): string {
  return randomBytes(48).toString('base64url');
}

const LOOPBACK = new Set(['127.0.0.1', 'localhost', '[::1]']);

/** True only for http(s) URLs on the exact app origin (loopback + the chosen UI port). */
export function isAppOrigin(url: string, uiPort: number): boolean {
  try {
    const u = new URL(url);
    return u.protocol === 'http:' && LOOPBACK.has(u.hostname) && u.port === String(uiPort);
  } catch {
    return false;
  }
}

/** Only https links may leave the app, via the OS browser. */
export function canOpenExternally(url: string): boolean {
  try {
    return new URL(url).protocol === 'https:';
  } catch {
    return false;
  }
}

/** Evidence reports are framed same-origin; the backend sends its own sandbox CSP for them. */
export function isEvidenceUrl(url: string): boolean {
  try {
    return /^\/api\/harness\/runs\/[^/]+\/evidence\//.test(new URL(url).pathname);
  } catch {
    return false;
  }
}

/** True only when `got` is exactly this launch's nonce (constant-time compare). */
export function nonceMatches(got: unknown, expected: string): boolean {
  if (typeof got !== 'string' || !expected) return false;
  const a = Buffer.from(got);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

/** Session cookie lifetime, seconds: matches frontend/lib/session.ts (SESSION_TTL_SECONDS). */
export const SESSION_TTL_SECONDS = 7 * 24 * 3600;

/** Per-port cookie name: frontend/lib/session.ts sessionCookieName. */
export function sessionCookieName(port: number | string): string {
  return `aqe_session_${port}`;
}

/** Session id from the Set-Cookie lines of /api/session for one port, or null. */
export function sessionCookieValue(setCookies: string[], port: number | string): string | null {
  const name = sessionCookieName(port);
  for (const line of setCookies) {
    const m = new RegExp(`(?:^|\\s)${name}=([A-Za-z0-9_.-]+)`).exec(line);
    if (m) return m[1] ?? null;
  }
  return null;
}

/** Exchanges the one-time launch secret for a session id. The secret is spent; the caller stores the cookie. */
export async function exchangeLaunchSecret(origin: string, secret: string): Promise<string> {
  const port = new URL(origin).port;
  const r = await fetch(`${origin}/api/session?launch=${encodeURIComponent(secret)}`, {
    redirect: 'manual', signal: AbortSignal.timeout(10_000),
  });
  const value = sessionCookieValue(r.headers.getSetCookie(), port);
  if (r.status !== 303 || !value) throw new Error('The UI did not accept the launch secret.');
  return value;
}

/** Homebrew (Apple silicon, Intel) and the usual /usr/local bin: where git, uv, java and python3 usually live on macOS. */
export const DARWIN_FALLBACK_PATH = ['/opt/homebrew/bin', '/usr/local/bin'];

/** A Finder or Dock launch gets launchd's minimal PATH; merge the login shell's PATH, the inherited one and the
 *  fallback dirs, first occurrence wins. Pure, so it can be tested without a shell. */
export function mergePath(loginPath: string | null, basePath: string, delimiter = ':'): string {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const dir of [...(loginPath ?? '').split(delimiter), ...basePath.split(delimiter), ...DARWIN_FALLBACK_PATH]) {
    if (dir && !seen.has(dir)) {
      seen.add(dir);
      out.push(dir);
    }
  }
  return out.join(delimiter);
}

/** The PATH of a login shell (`$SHELL -ilc`), or null when it cannot be read within 3 s. The value is wrapped in a
 *  marker so shell start-up noise on stdout cannot corrupt it. */
export function readLoginShellPath(shell: string): Promise<string | null> {
  return new Promise((resolve) => {
    execFile(shell, ['-ilc', "printf '__PATH__%s' \"$PATH\""], { timeout: 3000, encoding: 'utf8' }, (err, stdout) => {
      const m = err ? null : /__PATH__([^\n]*)/.exec(stdout);
      resolve(m ? m[1] ?? null : null);
    });
  });
}
