import { NextRequest } from 'next/server';
import { exchangeLaunchSecret, hostAllowed, isCrossSite, portOf, sessionSetCookie, unauthorizedPage } from '../../../lib/session';

export const dynamic = 'force-dynamic';

const html = { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' };

/** Exchanges the one-time launch secret for this port's session cookie, then leaves the secret out of the URL. */
export async function GET(request: NextRequest) {
  if (!hostAllowed(request) || isCrossSite(request)) return new Response(unauthorizedPage(), { status: 403, headers: html });
  const port = portOf(request);
  const value = exchangeLaunchSecret(request.nextUrl.searchParams.get('launch') ?? '', port);
  if (!value) return new Response(unauthorizedPage(), { status: 401, headers: html });
  const headers = new Headers({ Location: '/runs', 'Cache-Control': 'no-store' });
  headers.append('Set-Cookie', sessionSetCookie(port, value));
  return new Response(null, { status: 303, headers });
}
