import { defineConfig } from '@playwright/test';
import path from 'node:path';
const python = process.env.HARNESS_TEST_PYTHON || path.resolve('..', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const token = 'browser-fixture-token-32-characters-only';
export default defineConfig({
  testDir: './tests', workers: 1, timeout: 180000, expect: { timeout: 15000 },
  reporter: [['list'], ['html', { open: 'never' }], ['junit', { outputFile: 'test-results/junit.xml' }]],
  use: { actionTimeout: 15000, baseURL: 'http://127.0.0.1:3100', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  webServer: [
    { command: `"${python}" ../tests/ui_fixture_server.py`, url: 'http://127.0.0.1:8876/runs', env: { HARNESS_API_TOKEN: token }, timeout: 120000, stdout: 'pipe', stderr: 'pipe' },
    { command: 'npm run start -- --port 3100', url: 'http://127.0.0.1:3100', env: { HARNESS_API_TOKEN: token, HARNESS_API_PORT: '8876', NEXT_TELEMETRY_DISABLED: '1' }, timeout: 120000, stdout: 'pipe', stderr: 'pipe' },
  ],
});
