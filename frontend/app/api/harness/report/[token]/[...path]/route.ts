import { NextRequest } from 'next/server';
import { relayToBackend, validSegments } from '../../../../../../lib/backend';
import { hostAllowed, signingKey } from '../../../../../../lib/session';
import { reportTokenValid } from '../../../../../../lib/report-token';

export const dynamic = 'force-dynamic';

const denied = () => Response.json({ error: 'The report link has expired or is not valid for this file.' }, { status: 401 });

/**
 * Report files for one run, reached by the report frame with no session cookie. The token in the path is the only
 * credential: it must be valid for the run named in the path, the path must be one of that run's evidence files, and
 * only GET is served. Nothing else is reachable through this prefix.
 */
async function reportProxy(request: NextRequest, context: { params: Promise<{ token: string; path: string[] }> }) {
  if (!hostAllowed(request)) return denied();
  const { token, path } = await context.params;
  const runId = path[1] ?? '';
  const isEvidenceFile = path[0] === 'runs' && path[2] === 'evidence' && path.length >= 4 && /^[A-Za-z0-9_-]{1,100}$/.test(runId);
  if (!isEvidenceFile || !validSegments(path) || !reportTokenValid(token, runId, signingKey())) return denied();
  if (request.method !== 'GET') return Response.json({ error: 'Method not allowed.' }, { status: 405 });
  return relayToBackend(request, path);
}
export { reportProxy as GET, reportProxy as POST, reportProxy as DELETE };
