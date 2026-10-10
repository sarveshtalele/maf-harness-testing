import { NextRequest } from 'next/server';

export const dynamic = 'force-dynamic';
const segments = /^[A-Za-z0-9_.-]+$/;

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const origin = request.headers.get('origin');
  const host = request.headers.get('host') || '';
  const reportAsset = request.method === 'GET' && path[0] === 'runs' && path[2] === 'evidence' && path.length >= 5;
  if (!/^(localhost|127\.0\.0\.1):\d+$/.test(host) || (origin && origin !== `http://${host}` && !(reportAsset && origin === 'null')))
    return Response.json({ error: 'Only the local UI can access the harness.' }, { status: 403 });
  if (!path.every(part => segments.test(part) && part !== '.' && part !== '..'))
    return Response.json({ error: 'Invalid path.' }, { status: 400 });
  const token = process.env.HARNESS_API_TOKEN;
  if (!token) return Response.json({ error: 'Start the UI using scripts/start_ui.py so the local API is connected.' }, { status: 503 });
  const port = process.env.HARNESS_API_PORT || '8765';
  if (!/^\d{1,5}$/.test(port)) return Response.json({ error: 'Invalid local API port.' }, { status: 503 });
  try {
    const body = request.method === 'POST' ? await request.text() : undefined;
    if (body && Buffer.byteLength(body) > (['skills/import', 'runs'].includes(path.join('/')) ? 28000000 : 2097152)) return Response.json({ error: 'Request is too large.' }, { status: 413 });
    const response = await fetch(`http://127.0.0.1:${port}/${path.join('/')}${request.nextUrl.search}`, {
      method: request.method, headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body, cache: 'no-store', signal: AbortSignal.timeout(30000),
    });
    const headers = new Headers({ 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
    for (const name of ['content-type', 'content-disposition', 'content-security-policy', 'access-control-allow-origin']) {
      const value = response.headers.get(name);
      if (value) headers.set(name, value);
    }
    // Buffer the reply: streaming an HTTP/1.0 close-delimited body crashed undici (assert(!this.paused)) on Node 24.
    const payload = await response.arrayBuffer();
    return new Response(payload, { status: response.status, headers });
  } catch {
    return Response.json({ error: 'Local backend is unavailable. Check the launcher terminal and reconnect.' }, { status: 503 });
  }
}
export { proxy as GET, proxy as POST };
