// Server-only: capability tokens for report files. A sandboxed report frame (opaque origin) cannot send the session
// cookie, so the parent page asks for a token and puts it in the report's URL path. Relative asset URLs inside the
// report then keep the token prefix. A token is "<expiry>.<mac>", mac = HMAC(key, "report|runId|expiry").
import { mac, safeEqual, signingKey } from './session';

/** Lifetime of a report token: long enough to view a report, short enough that a copied URL goes stale. */
export const REPORT_TOKEN_TTL_SECONDS = 3600;

/** Pure: mints a token for one run. */
export function mintReportToken(key: Buffer, runId: string, expiry: number): string {
  return `${expiry}.${mac(key, ['report', runId, expiry])}`;
}

/** Pure: true only for this run, with the right MAC, and before the expiry. Constant time in the MAC. */
export function reportTokenValid(token: string, runId: string, key: Buffer, now = Date.now()): boolean {
  const m = /^(\d{10,})\.([A-Za-z0-9_-]{43})$/.exec(token);
  if (!m) return false;
  const expiry = Number(m[1]);
  if (expiry <= Math.floor(now / 1000)) return false;
  return safeEqual(m[2], mac(key, ['report', runId, expiry]));
}

/** A fresh token for a run, from the install's signing key. */
export function reportTokenFor(runId: string, now = Date.now()): string {
  return mintReportToken(signingKey(), runId, Math.floor(now / 1000) + REPORT_TOKEN_TTL_SECONDS);
}

/** The proxy prefix that carries a token. Report files live under it, as /runs/<id>/evidence/<hash>/<file>. */
export function reportPrefix(token: string): string {
  return `/api/harness/report/${token}`;
}
