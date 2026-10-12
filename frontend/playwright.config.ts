import { defineConfig } from '@playwright/test';
import path from 'node:path';
import fs from 'node:fs';
import os from 'node:os';
const python = process.env.HARNESS_TEST_PYTHON || path.resolve('..', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const token = 'browser-fixture-token-32-characters-only';
// Ports can be overridden so parallel worktrees never collide (scripts/check_all.py picks free ones).
const apiPort = process.env.PW_API_PORT || '8876';
const uiPort = process.env.PW_UI_PORT || '3100';
const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'maf-e2e-data-')); // MCP config and settings never touch the user's data directory
export default defineConfig({
  testDir: './tests', workers: 1, timeout: 180000, expect: { timeout: 15000 },
  reporter: [['list'], ['html', { open: 'never' }], ['junit', { outputFile: 'test-results/junit.xml' }]],
  use: { actionTimeout: 15000, baseURL: `http://127.0.0.1:${uiPort}`, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  webServer: [
    { command: `"${python}" ../tests/ui_fixture_server.py`, url: `http://127.0.0.1:${apiPort}/runs`, env: { HARNESS_API_TOKEN: token, HARNESS_DATA_DIR: dataDir, PW_API_PORT: apiPort }, timeout: 120000, stdout: 'pipe', stderr: 'pipe' },
    { command: `npm run start -- --port ${uiPort}`, url: `http://127.0.0.1:${uiPort}`, env: { HARNESS_API_TOKEN: token, HARNESS_API_PORT: apiPort, NEXT_TELEMETRY_DISABLED: '1' }, timeout: 120000, stdout: 'pipe', stderr: 'pipe' },
  ],
});
