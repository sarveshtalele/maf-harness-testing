import { NextRequest, NextResponse } from 'next/server';
import { contentSecurityPolicy, newNonce } from './lib/csp';
import { hasSession, isCrossSite, unauthorizedPage } from './lib/session';

// Every UI page needs the session cookie (the API proxy under /api checks it on its own). The nonce CSP is set here
// because a nonce is per request and cannot live in next.config.ts headers.
export function proxy(request: NextRequest) {
  const nonce = newNonce();
  const csp = contentSecurityPolicy(nonce, process.env.NODE_ENV === 'development');
  if (isCrossSite(request) || !hasSession(request)) {
    return new NextResponse(unauthorizedPage(), {
      status: 401,
      headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store', 'Content-Security-Policy': csp, 'X-Content-Type-Options': 'nosniff' },
    });
  }
  const headers = new Headers(request.headers);
  headers.set('x-nonce', nonce);
  headers.set('Content-Security-Policy', csp); // Next reads the nonce from the request's CSP during rendering
  const response = NextResponse.next({ request: { headers } });
  response.headers.set('Content-Security-Policy', csp);
  return response;
}

export const config = {
  // Not api/ (the proxy route checks its own session), not _next (static assets carry no secrets).
  matcher: ['/((?!api/|_next/|favicon.ico).*)'],
};
