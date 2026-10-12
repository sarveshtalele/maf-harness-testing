import fs from 'node:fs';
import path from 'node:path';
import { request } from '@playwright/test';
import { BASE_URL, LAUNCH_SECRET, SESSION_FILE } from './playwright.launch';

// Exchanges the launch secret once (the same request the launcher's browser makes) and saves the session cookie.
export default async function globalSetup() {
  const api = await request.newContext({ baseURL: BASE_URL });
  try {
    const response = await api.get(`/api/session?launch=${LAUNCH_SECRET}`, { maxRedirects: 0 });
    if (response.status() !== 303) throw new Error(`The launch secret was not accepted (HTTP ${response.status()}).`);
    fs.mkdirSync(path.dirname(SESSION_FILE), { recursive: true });
    await api.storageState({ path: SESSION_FILE });
  } finally {
    await api.dispose();
  }
}
