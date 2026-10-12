// Server-only: browser sessions for the local UI. Import from route handlers and proxy.ts only.
import { createHash, randomBytes, timingSafeEqual } from 'node:crypto';

/** HttpOnly session cookie set by /api/session after the one-time launch secret is exchanged. */
export const SESSION_COOKIE = 'aqe_session';
/** Env var holding the launch secret; the launcher or desktop shell generates it per launch. */
export const LAUNCH_SECRET_ENV = 'HARNESS_LAUNCH_SECRET';

interface SessionState {
  launchDigest: Buffer | null; // sha256 of the launch secret; null when the process was started without one
  launchUsed: boolean;
  sessionDigests: Set<string>; // sha256 of each session id: the ids themselves are never stored
}

const STATE_KEY = '__agenticQeSession';

function state(): SessionState {
  const holder = globalThis as unknown as Record<string, SessionState | undefined>;
  if (!holder[STATE_KEY]) {
    const secret = process.env[LAUNCH_SECRET_ENV] ?? '';
    holder[STATE_KEY] = {
      launchDigest: secret ? digest(secret) : null,
      launchUsed: false,
      sessionDigests: new Set(),
    };
  }
  return holder[STATE_KEY];
}

function digest(value: string): Buffer {
  return createHash('sha256').update(value).digest();
}

/**
 * Exchanges the launch secret for a session id. The secret works once: a second exchange, or a wrong secret, returns null.
 * The caller sets the returned id as the HttpOnly session cookie.
 */
export function exchangeLaunchSecret(secret: string): string | null {
  const s = state();
  if (!s.launchDigest || s.launchUsed || !secret) return null;
  if (!timingSafeEqual(digest(secret), s.launchDigest)) return null;
  s.launchUsed = true;
  const id = randomBytes(32).toString('base64url');
  s.sessionDigests.add(digest(id).toString('hex'));
  return id;
}

/** True when the request carries a session cookie issued by this process. */
export function hasSession(request: Request): boolean {
  const cookie = request.headers.get('cookie') ?? '';
  const match = new RegExp(`(?:^|;\\s*)${SESSION_COOKIE}=([A-Za-z0-9_-]{20,128})(?:;|$)`).exec(cookie);
  return !!match && state().sessionDigests.has(digest(match[1]).toString('hex'));
}

/** Host header allowlist (DNS rebinding): only loopback names on any port. */
export function hostAllowed(request: Request): boolean {
  return /^(localhost|127\.0\.0\.1):\d+$/.test(request.headers.get('host') ?? '');
}

/** Cross-site requests are never allowed: a page on another site cannot drive the local API or the UI. */
export function isCrossSite(request: Request): boolean {
  return request.headers.get('sec-fetch-site') === 'cross-site';
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
    + `or use the link it printed. Each link works once; start the launcher again for a new one.</p></main></body></html>`;
}
