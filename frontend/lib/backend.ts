// Server-only: relays one request to the local Python API with its bearer token. Callers check the caller first.
import { NextRequest } from 'next/server';

export const SEGMENT = /^[A-Za-z0-9_.-]+$/;

/** Path segments that the relay may forward (no traversal, no empty or dot segments). */
export function validSegments(path: string[]): boolean {
  return path.every(part => SEGMENT.test(part) && part !== '.' && part !== '..');
}

export async function relayToBackend(request: NextRequest, path: string[]): Promise<Response> {
  const token = process.env.HARNESS_API_TOKEN;
  if (!token) return Response.json({ error: 'Start the UI using scripts/start_ui.py so the local API is connected.' }, { status: 503 });
  const port = process.env.HARNESS_API_PORT || '8765';
  if (!/^\d{1,5}$/.test(port)) return Response.json({ error: 'Invalid local API port.' }, { status: 503 });
  const limit = ['skills/import', 'runs'].includes(path.join('/')) ? 28000000 : 2097152;
  const declared = Number(request.headers.get('content-length') || 0);
  if (declared > limit) return Response.json({ error: 'Request is too large.' }, { status: 413 });
  try {
    const body = request.method === 'POST' ? await request.text() : undefined;
    if (body && Buffer.byteLength(body) > limit) return Response.json({ error: 'Request is too large.' }, { status: 413 });
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
  } catch (error) {
    if (error instanceof Error && error.name === 'TimeoutError') return Response.json({ error: 'Local backend did not answer in time' }, { status: 504 });
    return Response.json({ error: 'Local backend is unavailable. Check the launcher terminal and reconnect.' }, { status: 503 });
  }
}
