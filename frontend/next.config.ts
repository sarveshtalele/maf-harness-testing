import path from 'node:path';
import type { NextConfig } from 'next';
// Standalone output is only for the desktop bundle; the dev launcher uses `next start`.
const config: NextConfig = {
  poweredByHeader: false,
  devIndicators: false,
  ...(process.env.DESKTOP_BUILD === '1' ? { output: 'standalone' as const, outputFileTracingRoot: path.resolve(__dirname) } : {}),
};
export default config;
