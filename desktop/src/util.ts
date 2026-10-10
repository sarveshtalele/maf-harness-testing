// Pure helpers: no Electron imports so they can be unit-tested with node:test.
import { randomBytes } from 'node:crypto';
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

export function csp(uiPort: number): string {
  const self = `http://127.0.0.1:${uiPort}`;
  return [
    `default-src 'self'`,
    // Next inlines bootstrap scripts/styles; tightening to nonces is a later hardening step.
    `script-src 'self' 'unsafe-inline'`,
    `style-src 'self' 'unsafe-inline'`,
    `img-src 'self' data: blob:`,
    `font-src 'self' data:`,
    `connect-src 'self' ${self}`,
    `object-src 'none'`,
    `base-uri 'self'`,
    `frame-ancestors 'self'`,
    `form-action 'self'`,
  ].join('; ');
}
