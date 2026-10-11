import type { ChildProcess } from 'node:child_process';
import { app, BrowserWindow, dialog, ipcMain, Menu, safeStorage, session, shell } from 'electron';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { KeyStore, ModelSettingsInput } from './keystore';
import { ProcessManager, ServiceSpec, waitForHttp } from './process-manager';
import { canOpenExternally, csp, isAppOrigin, isEvidenceUrl, makeToken, pickTwoPorts } from './util';

const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf8')) as {
  homepage: string;
};
const RELEASES_URL = `${pkg.homepage.replace(/\/$/, '')}/releases`;

let pm: ProcessManager;
let win: BrowserWindow | null = null;
let quitting = false;
let store: KeyStore;
let live: { backend: ServiceSpec; apiPort: number; token: string } | null = null;

// Packaged-app smoke mode (scripts/desktop_smoke.py): run the real boot, check the UI, print one verdict line, quit.
// The profile is a fresh temp dir, set before the single-instance lock and ready so a user or CI profile is untouched.
const SMOKE = process.env.AGENTIC_QE_SMOKE === '1';
const SMOKE_TIMEOUT_MS = 150_000;
const SMOKE_LOG_LINES = 40;
if (SMOKE) app.setPath('userData', fs.mkdtempSync(path.join(os.tmpdir(), 'agentic-qe-smoke-')));
let smokeDone = false;

function specs(apiPort: number, uiPort: number, token: string): { backend: ServiceSpec; ui: ServiceSpec } {
  const packaged = app.isPackaged;
  const repo = path.resolve(__dirname, '..', '..'); // dev: desktop/dist -> repo root
  const base: NodeJS.ProcessEnv = {
    ...process.env,
    HARNESS_API_TOKEN: token,
    HARNESS_API_PORT: String(apiPort),
    HARNESS_DATA_DIR: app.getPath('userData'),
    NEXT_TELEMETRY_DISABLED: '1',
    PYTHONUTF8: '1',
    PYTHONIOENCODING: 'utf-8',
    PYTHONUNBUFFERED: '1',
  };
  const uiEnv: NodeJS.ProcessEnv = {
    ...base,
    PORT: String(uiPort),
    HOSTNAME: '127.0.0.1',
    ELECTRON_RUN_AS_NODE: '1', // process.execPath acts as plain Node
  };
  if (packaged) {
    const res = process.resourcesPath;
    const exe = path.join(res, 'backend', process.platform === 'win32' ? 'maf-backend.exe' : 'maf-backend');
    return {
      backend: {
        name: 'backend', command: exe, args: ['--port', String(apiPort)], cwd: path.join(res, 'backend'),
        env: { ...base, ...store.env(), HARNESS_RESOURCE_ROOT: path.join(res, 'backend') },
      },
      ui: {
        name: 'frontend', command: process.execPath, args: [path.join(res, 'frontend', 'server.js')],
        // build_desktop.py ships the traced dependencies as deps/ (electron-builder drops node_modules).
        cwd: path.join(res, 'frontend'), env: { ...uiEnv, NODE_PATH: path.join(res, 'frontend', 'deps') },
      },
    };
  }
  const py = process.platform === 'win32'
    ? path.join(repo, '.venv', 'Scripts', 'python.exe')
    : path.join(repo, '.venv', 'bin', 'python');
  const fe = path.join(repo, 'frontend');
  const standalone = path.join(fe, '.next', 'standalone', 'server.js');
  const ui: ServiceSpec = fs.existsSync(standalone)
    ? { name: 'frontend', command: process.execPath, args: [standalone], cwd: path.dirname(standalone), env: uiEnv }
    : {
        // Fallback: `next start` (run `npm run build` in frontend/ first).
        name: 'frontend', command: process.execPath,
        args: [path.join(fe, 'node_modules', 'next', 'dist', 'bin', 'next'), 'start', '--hostname', '127.0.0.1', '--port', String(uiPort)],
        cwd: fe, env: uiEnv,
      };
  return {
    backend: {
      name: 'backend', command: py, args: ['-m', 'maf_test_harness.ui.api', '--port', String(apiPort)],
      cwd: repo, env: { ...base, ...store.env(), PYTHONPATH: path.join(repo, 'src') },
    },
    ui,
  };
}

/** False once the service exited or failed to spawn (e.g. ENOENT), so waits fail fast. */
const alive = (child: ChildProcess) => child.exitCode === null && !pm.hasSpawnError(child);

/** Running-run check for the settings handlers: a failed check must block the change, never pass as idle. */
async function runsBusy(apiPort: number, headers: Record<string, string>): Promise<boolean> {
  const r = await fetch(`http://127.0.0.1:${apiPort}/runs`, { headers }).catch(() => null);
  const body = r?.ok ? await r.json().catch(() => null) as { busy?: unknown } | null : null;
  if (typeof body?.busy !== 'boolean') throw new Error('Could not check whether a run is active; try again.');
  return body.busy;
}

function buildMenu(dataDir: string) {
  const template: Electron.MenuItemConstructorOptions[] = [
    ...(process.platform === 'darwin' ? [{ role: 'appMenu' as const }] : []),
    { role: 'editMenu' },
    { role: 'viewMenu' },
    { role: 'windowMenu' },
    {
      label: 'Help',
      submenu: [
        { label: 'Check for updates', click: () => void shell.openExternal(RELEASES_URL) },
        { label: 'Open data folder', click: () => void shell.openPath(dataDir) },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

async function boot(): Promise<{ apiPort: number; uiPort: number }> {
  const dataDir = app.getPath('userData');
  pm = new ProcessManager(path.join(dataDir, 'logs'));
  store = new KeyStore(path.join(dataDir, 'secrets.bin'), safeStorage);
  const [apiPort, uiPort] = await pickTwoPorts();
  const token = makeToken();
  const { backend, ui } = specs(apiPort, uiPort, token);

  live = { backend, apiPort, token };
  const be = pm.start(backend, [token, store.get().api_key, ...store.mcpValues()]);
  await waitForHttp(`http://127.0.0.1:${apiPort}/`, 'any', 90_000, () => alive(be)).catch(() => {
    throw new Error('backend did not start; see logs in ' + path.join(dataDir, 'logs'));
  });
  const fe = pm.start(ui, [token]);
  await waitForHttp(`http://127.0.0.1:${uiPort}/`, 200, 90_000, () => alive(fe));
  await waitForHttp(`http://127.0.0.1:${uiPort}/api/harness/config`, 200, 30_000);

  const origin = `http://127.0.0.1:${uiPort}`;
  session.defaultSession.webRequest.onHeadersReceived((details, cb) => {
    const headers = { ...details.responseHeaders };
    if (details.url.startsWith(origin) && !isEvidenceUrl(details.url)) headers['Content-Security-Policy'] = [csp(uiPort)];
    cb({ responseHeaders: headers });
  });
  session.defaultSession.setPermissionRequestHandler((_wc, _perm, cb) => cb(false));

  win = new BrowserWindow({
    width: 1400, height: 900, show: false, title: 'Agentic QE',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true,
    },
  });
  win.once('ready-to-show', () => win?.show());
  win.webContents.on('will-navigate', (e, url) => {
    if (!isAppOrigin(url, uiPort)) {
      e.preventDefault();
      if (canOpenExternally(url)) void shell.openExternal(url);
    }
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (canOpenExternally(url)) void shell.openExternal(url);
    return { action: 'deny' };
  });
  win.on('closed', () => { win = null; });
  await win.loadURL(origin);
  return { apiPort, uiPort };
}

function writeStdout(text: string): Promise<void> {
  return new Promise((resolve) => process.stdout.write(text, () => resolve()));
}

/** Smoke verdict: the UI must serve the harness config as a JSON object through the UI port. */
async function smokeCheckConfig(uiPort: number): Promise<void> {
  const r = await fetch(`http://127.0.0.1:${uiPort}/api/harness/config`, { signal: AbortSignal.timeout(10_000) });
  if (!r.ok) throw new Error(`/api/harness/config returned HTTP ${r.status}`);
  const body: unknown = await r.json().catch(() => undefined);
  if (!body || typeof body !== 'object' || Array.isArray(body)) throw new Error('/api/harness/config did not return a JSON object');
}

/** Prints the last lines of every service log (tokens are already redacted in the logs). */
async function dumpServiceLogs(): Promise<void> {
  const logDir = path.join(app.getPath('userData'), 'logs');
  let names: string[] = [];
  try { names = fs.readdirSync(logDir).filter((n) => n.startsWith('desktop-') && n.endsWith('.log')).sort(); } catch { /* no logs */ }
  for (const name of names) {
    let lines: string[] = [];
    try { lines = fs.readFileSync(path.join(logDir, name), 'utf8').split(/\r?\n/); } catch { /* unreadable */ }
    await writeStdout(`--- ${name} (last ${SMOKE_LOG_LINES} lines) ---\n${lines.slice(-SMOKE_LOG_LINES).join('\n')}\n`);
  }
}

/** Smoke failure: stop the services, show their logs, print the single FAIL line, exit 1. Runs once. */
async function smokeFail(reason: string): Promise<void> {
  if (smokeDone) return;
  smokeDone = true;
  if (pm) {
    await pm.stopAll().catch(() => undefined);
    await new Promise((r) => setTimeout(r, 300)); // let the log write streams flush before reading them
    await dumpServiceLogs();
  }
  await writeStdout(`AGENTIC_QE_SMOKE FAIL ${reason.replace(/\s+/g, ' ').trim()}\n`);
  app.exit(1);
}

/** Smoke success: print the verdict, then quit through the normal path (stop both trees, exit 0). */
async function smokeSucceed(apiPort: number, uiPort: number): Promise<void> {
  if (smokeDone) return;
  smokeDone = true;
  await writeStdout(`AGENTIC_QE_SMOKE OK backend=${apiPort} ui=${uiPort}\n`);
  quitting = true;
  await pm.stopAll().catch(() => undefined);
  app.exit(0);
}

ipcMain.handle('desktop:appVersion', () => app.getVersion());
ipcMain.handle('desktop:openDataFolder', () => shell.openPath(app.getPath('userData')).then(() => undefined));
ipcMain.handle('desktop:getModelSettings', () => store.view());
ipcMain.handle('desktop:saveModelSettings', async (_e, input: ModelSettingsInput) => {
  if (!live) throw new Error('Backend is not running.');
  const { apiPort, token } = live;
  if (await runsBusy(apiPort, { Authorization: `Bearer ${token}` })) throw new Error('Wait for the current operation to finish before changing model settings.');
  const view = store.save(input); // validates before anything restarts
  const backend = specs(apiPort, 0, token).backend; // re-reads the new key into the spawn env
  live.backend = backend;
  const child = await pm.restart(backend, [token, store.get().api_key, ...store.mcpValues()]);
  await waitForHttp(`http://127.0.0.1:${apiPort}/`, 'any', 60_000, () => alive(child)).catch(() => {
    throw new Error(`Backend did not restart; see logs in ${path.join(app.getPath('userData'), 'logs')}`);
  });
  return view;
});
ipcMain.handle('desktop:getMcpSecrets', () => store.mcpView());
ipcMain.handle('desktop:saveMcpSecrets', async (_e, server: string, values: Record<string, string>) => {
  if (!live) throw new Error('Backend is not running.');
  const { apiPort, token } = live;
  const auth = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
  if (await runsBusy(apiPort, auth)) throw new Error('Wait for the current operation to finish before changing MCP secrets.');
  const clean = store.checkMcpSecrets(server, values); // validate before anything is sent or stored
  pm.addRedactions(Object.values(clean)); // never let a value reach a service log, whatever happens next
  // Backend first (in memory, no restart): it also checks the server exists and the secret name is valid.
  const r = await fetch(`http://127.0.0.1:${apiPort}/mcp-servers/${encodeURIComponent(server)}/secrets`, { method: 'POST', headers: auth, body: JSON.stringify(clean) });
  if (!r.ok) throw new Error(((await r.json().catch(() => ({}))) as { error?: string }).error || 'Backend rejected the secrets.');
  const view = store.saveMcpSecrets(server, clean); // only now persist to the keychain (a later spawn gets them via env())
  pm.addRedactions(store.mcpValues());
  return view;
});
ipcMain.handle('desktop:deleteMcpServer', async (_e, server: string) => {
  if (!live) throw new Error('Backend is not running.');
  const { apiPort, token } = live;
  store.checkMcpSecrets(server, {}); // validates the name
  const r = await fetch(`http://127.0.0.1:${apiPort}/mcp-servers/${encodeURIComponent(server)}`, { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } });
  if (!r.ok) throw new Error(((await r.json().catch(() => ({}))) as { error?: string }).error || 'Backend refused the delete.');
  store.removeMcpServer(server); // the keychain forgets its secrets only after the backend deleted the server
  return store.mcpView();
});
ipcMain.handle('desktop:checkForUpdates', () => shell.openExternal(RELEASES_URL));

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (win) { if (win.isMinimized()) win.restore(); win.focus(); }
  });
  app.whenReady().then(async () => {
    buildMenu(app.getPath('userData'));
    if (SMOKE) setTimeout(() => void smokeFail(`timed out after ${SMOKE_TIMEOUT_MS / 1000} s`), SMOKE_TIMEOUT_MS);
    try {
      const { apiPort, uiPort } = await boot();
      if (SMOKE) {
        await smokeCheckConfig(uiPort);
        await smokeSucceed(apiPort, uiPort);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (SMOKE) {
        await smokeFail(message);
        return;
      }
      dialog.showErrorBox('Agentic QE failed to start', `${message}\n\nLogs: ${path.join(app.getPath('userData'), 'logs')}`);
      app.quit();
    }
  });
  app.on('window-all-closed', () => app.quit());
  // A terminal Ctrl+C, `kill` or OS logout sends a signal that skips Electron's quit path: route it through
  // before-quit so the backend and UI process trees are stopped instead of orphaned.
  for (const sig of ['SIGINT', 'SIGTERM', 'SIGHUP'] as const) process.on(sig, () => app.quit());
  app.on('before-quit', (e) => {
    if (quitting || !pm) return;
    e.preventDefault();
    quitting = true;
    void pm.stopAll().finally(() => app.exit(0));
  });
}
