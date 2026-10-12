// Server-only: the Content-Security-Policy for every UI document (launcher and desktop alike).
import { randomBytes } from 'node:crypto';

/** A fresh nonce per document: Next applies it to its own inline bootstrap scripts (content-security-policy guide). */
export function newNonce(): string {
  return randomBytes(16).toString('base64');
}

export function contentSecurityPolicy(nonce: string, development = false): string {
  return [
    "default-src 'self'",
    // No 'unsafe-inline' for scripts: Next's nonce covers its bootstrap scripts and the FIRST_PAINT script in layout.tsx.
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${development ? " 'unsafe-eval'" : ''}`,
    // Next inlines style attributes; scripts are the risk this policy closes, so styles keep 'unsafe-inline'.
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src 'self'${development ? ' ws://127.0.0.1:* ws://localhost:*' : ''}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'self'",
  ].join('; ');
}
