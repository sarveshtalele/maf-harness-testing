import path from 'node:path';
import type { NextConfig } from 'next';
// Standalone output is only for the desktop bundle; the dev launcher uses `next start`.
const config: NextConfig = {
  poweredByHeader: false,
  devIndicators: false,
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
