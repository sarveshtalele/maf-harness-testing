// Shared by playwright.config.ts and playwright.global-setup.ts. Ports come from the one scheme used by
// scripts/check_all.py (PW_UI_PORT, PW_API_PORT); the defaults are the CI values.
import path from 'node:path';

export const UI_PORT = Number(process.env.PW_UI_PORT || 3100);
export const API_PORT = Number(process.env.PW_API_PORT || 8876);
export const BASE_URL = `http://127.0.0.1:${UI_PORT}`;
/** Known to the test only: the UI server gets it as HARNESS_LAUNCH_SECRET and the global setup exchanges it once. */
export const LAUNCH_SECRET = 'browser-fixture-launch-secret-0123456789abcdef';
/** Session cookie saved after the exchange; every spec loads it through `use.storageState`. */
export const SESSION_FILE = path.resolve(__dirname, 'test-results', 'session.json');
