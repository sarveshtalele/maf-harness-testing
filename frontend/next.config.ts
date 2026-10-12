import path from 'node:path';
import type { NextConfig } from 'next';
// Standalone output is only for the desktop bundle; the dev launcher uses `next start`.
const config: NextConfig = {
  poweredByHeader: false,
  devIndicators: false,
  // Old single-page links (?run=&tab=&stage= and ?page=) go to the App Router routes. Tab rules come first.
  async redirects() {
    const tabs: [string, string][] = [['Errors', 'errors'], ['Artifacts', 'artifacts'], ['Generated tests', 'tests'], ['QA report', 'qa'], ['Test reports', 'evidence'], ['Logs', 'logs'], ['Observability', 'observability'], ['Audit', 'audit'], ['Prompts', 'prompts'], ['Review', '']];
    const run = (tab: string, segment: string) => ({ source: '/', has: [{ type: 'query' as const, key: 'run', value: '(?<id>[A-Za-z0-9_-]{1,100})' }, { type: 'query' as const, key: 'tab', value: tab }], destination: segment ? `/runs/:id/${segment}` : '/runs/:id', permanent: false });
    return [
      { source: '/', has: [{ type: 'query' as const, key: 'run', value: '(?<id>[A-Za-z0-9_-]{1,100})' }, { type: 'query' as const, key: 'tab', value: 'Artifacts' }, { type: 'query' as const, key: 'stage', value: '(?<stage>[a-z_]{1,40})' }], destination: '/runs/:id/artifacts/:stage', permanent: false },
      ...tabs.map(([tab, segment]) => run(tab, segment)),
      { source: '/', has: [{ type: 'query' as const, key: 'run', value: '(?<id>[A-Za-z0-9_-]{1,100})' }], destination: '/runs/:id', permanent: false },
      { source: '/', has: [{ type: 'query' as const, key: 'page', value: 'new' }], destination: '/runs/new', permanent: false },
      { source: '/', has: [{ type: 'query' as const, key: 'page', value: 'prompts' }], destination: '/prompts', permanent: false },
      { source: '/', has: [{ type: 'query' as const, key: 'page', value: 'configuration' }], destination: '/settings', permanent: false },
      { source: '/', has: [{ type: 'query' as const, key: 'page', value: 'marketplace' }, { type: 'query' as const, key: 'tab', value: 'mcp' }], destination: '/marketplace/mcp', permanent: false },
      { source: '/', has: [{ type: 'query' as const, key: 'page', value: 'marketplace' }], destination: '/marketplace/skills', permanent: false },
      { source: '/', has: [{ type: 'query' as const, key: 'page', value: 'skills' }, { type: 'query' as const, key: 'skill', value: '(?<skill>[^&]{1,200})' }], destination: '/marketplace/skills/:skill', permanent: false },
      { source: '/', has: [{ type: 'query' as const, key: 'page', value: 'skills' }], destination: '/marketplace/skills', permanent: false },
      // Marketplace links from before the sub-routes: ?tab=mcp and ?skill= move to the routes; the bare page opens skills.
      { source: '/marketplace', has: [{ type: 'query' as const, key: 'tab', value: 'mcp' }], destination: '/marketplace/mcp', permanent: false },
      { source: '/marketplace', has: [{ type: 'query' as const, key: 'skill', value: '(?<skill>[^&]{1,200})' }], destination: '/marketplace/skills/:skill', permanent: false },
      { source: '/marketplace', destination: '/marketplace/skills', permanent: false },
    ];
  },
  // Only same-origin framing: the evidence report iframe is same-origin; approvals must not be framed elsewhere.
  async headers() {
    // Not /api/: the proxy passes the backend's own evidence CSP through and must keep it.
    return [{ source: '/((?!api/).*)', headers: [
      { key: 'Content-Security-Policy', value: "frame-ancestors 'self'" },
      { key: 'X-Frame-Options', value: 'SAMEORIGIN' },
    ] }];
  },
  // Desktop bundle: installed under Program Files, read-only for standard users, so the server must never write
  // .next/cache there. The app uses no next/image and no ISR (checked by tests/test_desktop_next_cache.py); these two
  // options only switch off the image optimizer's disk cache and ISR flush to disk.
  ...(process.env.DESKTOP_BUILD === '1' ? {
    output: 'standalone' as const,
    outputFileTracingRoot: path.resolve(__dirname),
    images: { maximumDiskCacheSize: 0 },
    experimental: { isrFlushToDisk: false },
  } : {}),
};
export default config;
