import { NextRequest } from 'next/server';
import { relayToBackend, validSegments } from '../../../../lib/backend';
import { hasSession, hostAllowed, isCrossSite } from '../../../../lib/session';

export const dynamic = 'force-dynamic';

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const origin = request.headers.get('origin');
  const host = request.headers.get('host') || '';
  // No Origin exception: a report frame reads its files through /api/harness/report/<token>/ (report/[token]/).
  if (!hostAllowed(request) || (origin && origin !== `http://${host}`))
    return Response.json({ error: 'Only the local UI can access the harness.' }, { status: 403 });
  if (isCrossSite(request)) return Response.json({ error: 'Cross-site requests are not allowed.' }, { status: 403 });
  if (!hasSession(request)) {
    return Response.json({ error: 'Open the app from the launcher (start-windows.cmd / start-macos.command / start-linux.sh), or use the link it printed.' }, { status: 401 });
  }
  if (!validSegments(path)) return Response.json({ error: 'Invalid path.' }, { status: 400 });
  return relayToBackend(request, path);
}
export { proxy as GET, proxy as POST, proxy as DELETE };
