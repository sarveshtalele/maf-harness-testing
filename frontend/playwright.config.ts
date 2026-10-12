import { defineConfig } from '@playwright/test';
import path from 'node:path';
import fs from 'node:fs';
import os from 'node:os';
import { API_PORT, BASE_URL, LAUNCH_SECRET, SESSION_FILE, UI_PORT } from './playwright.launch';
const python = process.env.HARNESS_TEST_PYTHON || path.resolve('..', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const token = 'browser-fixture-token-32-characters-only';
const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'maf-e2e-data-')); // MCP config and settings never touch the user's data directory
export default defineConfig({
  testDir: './tests', globalSetup: './playwright.global-setup.ts', workers: 1, timeout: 180000, expect: { timeout: 15000 },
  reporter: [['list'], ['html', { open: 'never' }], ['junit', { outputFile: 'test-results/junit.xml' }]],
  use: { actionTimeout: 15000, baseURL: BASE_URL, storageState: SESSION_FILE, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  webServer: [
    { command: `"${python}" ../tests/ui_fixture_server.py`, url: `http://127.0.0.1:${API_PORT}/runs`, env: { HARNESS_API_TOKEN: token, HARNESS_DATA_DIR: dataDir, PW_API_PORT: String(API_PORT) }, timeout: 120000, stdout: 'pipe', stderr: 'pipe' },
    { command: `npm run start -- --port ${UI_PORT}`, url: BASE_URL, env: { HARNESS_API_TOKEN: token, HARNESS_API_PORT: String(API_PORT), HARNESS_LAUNCH_SECRET: LAUNCH_SECRET, NEXT_TELEMETRY_DISABLED: '1' }, timeout: 120000, stdout: 'pipe', stderr: 'pipe' },
  ],
});
