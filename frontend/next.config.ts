import path from 'node:path';
import type { NextConfig } from 'next';
// Standalone output is only for the desktop bundle; the dev launcher uses `next start`.
const config: NextConfig = {
  poweredByHeader: false,
  devIndicators: false,
  // Only same-origin framing: the evidence report iframe is same-origin; approvals must not be framed elsewhere.
  async headers() {
    return [{ source: '/:path*', headers: [
      { key: 'Content-Security-Policy', value: "frame-ancestors 'self'" },
      { key: 'X-Frame-Options', value: 'SAMEORIGIN' },
    ] }];
  },
  ...(process.env.DESKTOP_BUILD === '1' ? { output: 'standalone' as const, outputFileTracingRoot: path.resolve(__dirname) } : {}),
};
export default config;
