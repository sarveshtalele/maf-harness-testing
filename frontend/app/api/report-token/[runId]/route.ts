import { NextRequest } from 'next/server';
import { hasSession, hostAllowed, isCrossSite } from '../../../../lib/session';
import { reportPrefix, reportTokenFor } from '../../../../lib/report-token';

export const dynamic = 'force-dynamic';

/** Gives a signed-in page the URL prefix for one run's report files (a short-lived token in the path). */
export async function GET(request: NextRequest, context: { params: Promise<{ runId: string }> }) {
  if (!hostAllowed(request) || isCrossSite(request)) return Response.json({ error: 'Not allowed.' }, { status: 403 });
  if (!hasSession(request)) return Response.json({ error: 'Open the app from the launcher, or use the link it printed.' }, { status: 401 });
  const { runId } = await context.params;
  if (!/^[A-Za-z0-9_-]{1,100}$/.test(runId)) return Response.json({ error: 'Invalid run id.' }, { status: 400 });
  return Response.json({ prefix: reportPrefix(reportTokenFor(runId)) }, { headers: { 'Cache-Control': 'no-store' } });
}
