import type { ChildProcess } from 'node:child_process';
import { app, BrowserWindow, dialog, ipcMain, IpcMainInvokeEvent, Menu, safeStorage, session, shell } from 'electron';
import * as fs from 'node:fs';
import * as os from 'node:os';
import { randomBytes } from 'node:crypto';
import * as path from 'node:path';
import { KeyStore, ModelSettingsInput } from './keystore';
import { ProcessManager, reapOrphans, ServiceSpec, waitForHttp } from './process-manager';
import { canOpenExternally, exchangeLaunchSecret, isAppOrigin, makeToken, mergePath, nonceMatches, pickTwoPorts, readLoginShellPath, SESSION_TTL_SECONDS, sessionCookieName } from './util';
import { denyPermissionCheck, denyPermissionRequest, isFrameNavigationAllowed, isTrustedSender } from './guards';

const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf8')) as {
  homepage: string;
};
const RELEASES_URL = `${pkg.homepage.replace(/\/$/, '')}/releases`;

let pm: ProcessManager;
let win: BrowserWindow | null = null;
let uiPort: number | null = null;
let quitting = false;
let exitCode = 0;
let store: KeyStore;
let live: { backend: ServiceSpec; apiPort: number; token: string; nonce: string } | null = null;
let settingsBusy = false;
/** The window's session id (F1); the smoke check reads the UI through the same session. */
let uiSessionId = '';
/** macOS only: PATH for the services, merged from the login shell (a Finder launch gets launchd's minimal PATH). */
let servicePath: string | undefined;
/** Per-launch id on the backend command line (not the API token, which must stay out of argv). Recognises orphans. */
let instanceId = '';

// Packaged-app smoke mode (scripts/desktop_smoke.py): run the real boot, check the UI, print one verdict line, quit.
// The profile is a fresh temp dir, set before the single-instance lock and ready so a user or CI profile is untouched.
const SMOKE = process.env.AGENTIC_QE_SMOKE === '1';
const SMOKE_TIMEOUT_MS = 150_000;
const SMOKE_LOG_LINES = 40;
const smokeDir = SMOKE ? fs.mkdtempSync(path.join(os.tmpdir(), 'agentic-qe-smoke-')) : null;
if (smokeDir) app.setPath('userData', smokeDir);
let smokeDone = false;

/** The backend prints this line only after its own socket bound the port (ui/api.py), so a foreign listener cannot satisfy it. */
const BACKEND_READY = /Local harness API: http:\/\/127\.0\.0\.1:(\d+)(?: nonce=(\S+))?/;
/** Exits the UI when the shell closes its stdin pipe (shell crashed or was killed). Written into userData at boot. */
const PARENT_EXIT_SOURCE = `// Written by the Agentic QE desktop shell: stop when the shell's stdin pipe closes.
process.stdin.on('end', () => process.exit(0));
process.stdin.on('error', () => process.exit(0));
process.stdin.resume();
`;
const LOADING_HTML = '<!doctype html><meta charset="utf-8"><title>Agentic QE</title>'
  + '<body style="font-family:system-ui,sans-serif;margin:0;display:grid;place-items:center;height:100vh;background:#fff;color:#333">'
  + '<p>Starting Agentic QE...</p></body>';

function specs(apiPort: number, uiPortNum: number, token: string, launchSecret = '', nonce = ''): { backend: ServiceSpec; ui: ServiceSpec } {
  const packaged = app.isPackaged;
  const repo = path.resolve(__dirname, '..', '..'); // dev: desktop/dist -> repo root
  // The preload path is in the Next command line and unique to this user-data folder: it is the UI's orphan marker.
  const preload = path.join(app.getPath('userData'), 'parent-exit.cjs');
  const base: NodeJS.ProcessEnv = {
    ...process.env,
    HARNESS_API_TOKEN: token,
    HARNESS_API_PORT: String(apiPort),
    HARNESS_DATA_DIR: app.getPath('userData'),
    HARNESS_EXIT_ON_STDIN_EOF: '1', // desktop_backend.py: exit when the shell's stdin pipe closes
    ...(servicePath ? { PATH: servicePath } : {}),
    NEXT_TELEMETRY_DISABLED: '1',
    PYTHONUTF8: '1',
    PYTHONIOENCODING: 'utf-8',
    PYTHONUNBUFFERED: '1',
  };
  const uiEnv: NodeJS.ProcessEnv = {
    ...base,
    PORT: String(uiPortNum),
    HOSTNAME: '127.0.0.1',
    ELECTRON_RUN_AS_NODE: '1', // process.execPath acts as plain Node
    // Only the UI gets the launch secret: the window exchanges it once for the session cookie (F1).
    ...(launchSecret ? { HARNESS_LAUNCH_SECRET: launchSecret } : {}),
    // Keeps the signing key and the spent launch secret across a restart of the UI server (0600 file).
    HARNESS_UI_STATE_DIR: app.getPath('userData'),
  };
  if (packaged) {
    const res = process.resourcesPath;
    const exe = path.join(res, 'backend', process.platform === 'win32' ? 'maf-backend.exe' : 'maf-backend');
    return {
      backend: {
        name: 'backend', command: exe, args: ['--port', String(apiPort), '--instance', instanceId], cwd: path.join(res, 'backend'),
        env: { ...base, ...store.env(), HARNESS_LAUNCH_NONCE: nonce, HARNESS_RESOURCE_ROOT: path.join(res, 'backend') },
        port: apiPort, marker: instanceId,
      },
      ui: {
        name: 'frontend', command: process.execPath, args: ['--require', preload, path.join(res, 'frontend', 'server.js')],
        // build_desktop.py ships the traced dependencies as deps/ (electron-builder drops node_modules).
        cwd: path.join(res, 'frontend'), env: { ...uiEnv, NODE_PATH: path.join(res, 'frontend', 'deps') },
        port: uiPortNum, marker: preload,
      },
    };
  }
  const py = process.platform === 'win32'
    ? path.join(repo, '.venv', 'Scripts', 'python.exe')
    : path.join(repo, '.venv', 'bin', 'python');
  const fe = path.join(repo, 'frontend');
  const standalone = path.join(fe, '.next', 'standalone', 'server.js');
  const ui: ServiceSpec = fs.existsSync(standalone)
    ? {
        name: 'frontend', command: process.execPath, args: ['--require', preload, standalone],
        cwd: path.dirname(standalone), env: uiEnv, port: uiPortNum, marker: preload,
      }
    : {
        // Fallback: `next start` (run `npm run build` in frontend/ first).
        name: 'frontend', command: process.execPath,
        args: ['--require', preload, path.join(fe, 'node_modules', 'next', 'dist', 'bin', 'next'), 'start', '--hostname', '127.0.0.1', '--port', String(uiPortNum)],
        cwd: fe, env: uiEnv, port: uiPortNum, marker: preload,
      };
  return {
    backend: {
      name: 'backend', command: py, args: ['-m', 'maf_test_harness.desktop_backend', '--port', String(apiPort), '--instance', instanceId],
      cwd: repo, env: { ...base, ...store.env(), HARNESS_LAUNCH_NONCE: nonce, PYTHONPATH: path.join(repo, 'src') },
      port: apiPort, marker: instanceId,
    },
    ui,
  };
}

/** False once the service exited or failed to spawn (e.g. ENOENT), so waits fail fast. */
const alive = (child: ChildProcess) => child.exitCode === null && !pm.hasSpawnError(child);

/** Waits for the backend's own ready line: it must name our port and echo this launch's nonce (F13). Only our child
 *  writes to its stdout pipe, so a process that merely holds the port cannot produce the line; the token is sent after this. */
async function waitBackendReady(child: ChildProcess, apiPort: number, timeoutMs: number, nonce: string): Promise<void> {
  const m = await pm.waitForOutput('backend', BACKEND_READY, timeoutMs, () => alive(child));
  if (Number(m[1]) !== apiPort) throw new Error(`backend reported port ${m[1]}, expected ${apiPort}`);
  if (!nonceMatches(m[2] ?? '', nonce)) throw new Error('Another process answered on the API port. Startup was stopped.');
}

/** Authenticated echo of the nonce (GET /health), checked before the UI or any request relies on this API. */
async function verifyBackend(apiPort: number, token: string, nonce: string): Promise<void> {
  const r = await fetch(`http://127.0.0.1:${apiPort}/health`, { headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(5_000) });
  const body: unknown = r.ok ? await r.json().catch(() => undefined) : undefined;
  if (!nonceMatches(typeof body === 'object' && body !== null ? (body as { nonce?: unknown }).nonce : undefined, nonce)) {
    throw new Error('Another process answered on the API port. Startup was stopped.');
  }
}

/** MCP server name -> target fingerprint, as the backend holds the definitions now (no secrets). */
async function mcpFingerprints(apiPort: number, token: string): Promise<Record<string, string>> {
  const r = await fetch(`http://127.0.0.1:${apiPort}/mcp-servers`, { headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(10_000) });
  if (!r.ok) throw new Error('Could not read the MCP servers from the local API.');
  const body = (await r.json()) as { servers?: { name?: unknown; fingerprint?: unknown }[] };
  const out: Record<string, string> = {};
  for (const server of body.servers ?? []) {
    if (typeof server.name === 'string' && typeof server.fingerprint === 'string') out[server.name] = server.fingerprint;
  }
  return out;
}

/** Hands the backend the MCP secrets whose target still matches (F4). Runs after every backend start, before any run. */
async function provisionMcp(apiPort: number, token: string): Promise<void> {
  store.reconcileMcp(await mcpFingerprints(apiPort, token));
  const auth = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
  for (const [server, values] of Object.entries(store.get().mcp)) {
    // A rejected server keeps its secrets in the keychain only; the backend then reports it as not ready.
    await fetch(`http://127.0.0.1:${apiPort}/mcp-servers/${encodeURIComponent(server)}/secrets`, { method: 'POST', headers: auth, body: JSON.stringify(values), signal: AbortSignal.timeout(10_000) }).catch(() => undefined);
  }
  pm.addRedactions(store.mcpValues());
}

/** The UI must reach this launch's API through the session, so a squatter on the UI port cannot pass (F13). */
async function verifyUi(origin: string, sessionId: string, nonce: string): Promise<void> {
  const r = await fetch(`${origin}/api/harness/health`, { headers: { Cookie: `${sessionCookieName(new URL(origin).port)}=${sessionId}` }, signal: AbortSignal.timeout(30_000) });
  const body: unknown = r.ok ? await r.json().catch(() => undefined) : undefined;
  if (!nonceMatches(typeof body === 'object' && body !== null ? (body as { nonce?: unknown }).nonce : undefined, nonce)) {
    throw new Error('The UI is not connected to this launch\'s local API.');
  }
}

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

/** The window exists from the first moment, so a second launch during the boot has something to focus. */
function createWindow(): BrowserWindow {
  const w = new BrowserWindow({
    width: 1400, height: 900, show: true, title: 'Agentic QE',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true,
    },
  });
  void w.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(LOADING_HTML)}`);
  w.webContents.on('will-navigate', (e, url) => {
    if (uiPort === null || !isAppOrigin(url, uiPort)) {
      e.preventDefault();
      if (canOpenExternally(url)) void shell.openExternal(url);
    }
  });
  w.webContents.on('will-frame-navigate', (details) => {
    if (!isFrameNavigationAllowed(details.url, uiPort)) details.preventDefault();
  });
  w.webContents.setWindowOpenHandler(({ url }) => {
    if (canOpenExternally(url)) void shell.openExternal(url);
    return { action: 'deny' };
  });
  w.on('closed', () => { win = null; });
  win = w;
  return w;
}

async function boot(): Promise<{ apiPort: number; uiPort: number }> {
  const dataDir = app.getPath('userData');
  if (process.platform === 'darwin') {
    servicePath = mergePath(await readLoginShellPath(process.env.SHELL || '/bin/zsh'), process.env.PATH ?? '');
  }
  pm = new ProcessManager(path.join(dataDir, 'logs'), path.join(dataDir, 'children.json'));
  // A previous instance that crashed or was killed may have left services behind: stop them before starting new ones.
  await reapOrphans(path.join(dataDir, 'children.json'));
  fs.writeFileSync(path.join(dataDir, 'parent-exit.cjs'), PARENT_EXIT_SOURCE, { encoding: 'utf8', mode: 0o600 });
  store = new KeyStore(path.join(dataDir, 'secrets.bin'), safeStorage);
  const [apiPort, uiPortNum] = await pickTwoPorts();
  const token = makeToken();
  // Single use: the window exchanges it for the session cookie below. It is never a command-line argument.
  const launchSecret = randomBytes(32).toString('base64url');
  instanceId = randomBytes(12).toString('hex');
  const nonce = randomBytes(24).toString('base64url');
  const { backend, ui } = specs(apiPort, uiPortNum, token, launchSecret, nonce);

  live = { backend, apiPort, token, nonce };
  const be = pm.start(backend, [token, store.get().api_key, ...store.mcpValues(), nonce]);
  await waitBackendReady(be, apiPort, 90_000, nonce).catch((err: unknown) => {
    if (err instanceof Error && err.message.startsWith('Another process')) throw err;
    throw new Error('backend did not start; see logs in ' + path.join(dataDir, 'logs'));
  });
  await verifyBackend(apiPort, token, nonce);
  await provisionMcp(apiPort, token);
  const fe = pm.start(ui, [token, launchSecret]);
  // Any HTTP reply counts: without a session the UI answers 401 by design (proxy.ts).
  await waitForHttp(`http://127.0.0.1:${uiPortNum}/`, 'any', 90_000, () => alive(fe));

  const origin = `http://127.0.0.1:${uiPortNum}`;
  // The window needs no user action: the launch secret becomes the HttpOnly session cookie before the first load.
  const sessionId = await exchangeLaunchSecret(origin, launchSecret);
  await verifyUi(origin, sessionId, nonce);
  uiSessionId = sessionId;
  // Persistent for a week, so a reopened browser works; per port, so another local app cannot clobber it.
  await session.defaultSession.cookies.set({
    url: origin, name: sessionCookieName(uiPortNum), value: sessionId, httpOnly: true, sameSite: 'strict', path: '/',
    expirationDate: Math.floor(Date.now() / 1000) + SESSION_TTL_SECONDS,
  });
  uiPort = uiPortNum;

  session.defaultSession.setPermissionRequestHandler(denyPermissionRequest);
  session.defaultSession.setPermissionCheckHandler(denyPermissionCheck);

  const w = win ?? createWindow();
  await w.loadURL(origin);
  return { apiPort, uiPort: uiPortNum };
}

function writeStdout(text: string): Promise<void> {
  return new Promise((resolve) => process.stdout.write(text, () => resolve()));
}

/** Smoke verdict: the UI must serve the harness config as a JSON object through the UI port. */
async function smokeCheckConfig(uiPortNum: number): Promise<void> {
  const r = await fetch(`http://127.0.0.1:${uiPortNum}/api/harness/config`, {
    headers: { Cookie: `${sessionCookieName(uiPortNum)}=${uiSessionId}` }, signal: AbortSignal.timeout(10_000),
  });
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

/** Removes the smoke profile this process created (never a user profile). */
function removeSmokeProfile(): void {
  if (smokeDir) fs.rmSync(smokeDir, { recursive: true, force: true });
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
  removeSmokeProfile();
  await writeStdout(`AGENTIC_QE_SMOKE FAIL ${reason.replace(/\s+/g, ' ').trim()}\n`);
  app.exit(1);
}

/** Smoke success: print the verdict, then quit through the normal path (stop both trees, exit 0). */
async function smokeSucceed(apiPort: number, uiPortNum: number): Promise<void> {
  if (smokeDone) return;
  smokeDone = true;
  await writeStdout(`AGENTIC_QE_SMOKE OK backend=${apiPort} ui=${uiPortNum}\n`);
  quitting = true;
  await pm.stopAll().catch(() => undefined);
  removeSmokeProfile();
  app.exit(0);
}


function handle<A extends unknown[], R>(channel: string, listener: (...args: A) => R): void {
  ipcMain.handle(channel, (e: IpcMainInvokeEvent, ...args: unknown[]) => {
    if (!isTrustedSender(e, uiPort)) throw new Error('This page may not use the desktop bridge.');
    return listener(...(args as A));
  });
}

handle('desktop:appVersion', () => app.getVersion());
handle('desktop:openDataFolder', () => shell.openPath(app.getPath('userData')).then(() => undefined));
handle('desktop:getModelSettings', () => store.view());
handle('desktop:saveModelSettings', async (input: ModelSettingsInput) => {
  if (!live) throw new Error('Backend is not running.');
  if (settingsBusy) throw new Error('A settings change is already in progress.');
  settingsBusy = true;
  try {
    const { apiPort, token, nonce } = live;
    if (await runsBusy(apiPort, { Authorization: `Bearer ${token}` })) throw new Error('Wait for the current operation to finish before changing model settings.');
    const view = store.save(input); // validates before anything restarts
    const backend = specs(apiPort, 0, token, '', nonce).backend; // re-reads the new key into the spawn env
    live.backend = backend;
    const child = await pm.restart(backend, [token, store.get().api_key, ...store.mcpValues(), nonce]);
    await waitBackendReady(child, apiPort, 60_000, nonce).catch((err: unknown) => {
      if (err instanceof Error && err.message.startsWith('Another process')) throw err;
      throw new Error(`Backend did not restart; see logs in ${path.join(app.getPath('userData'), 'logs')}`);
    });
    await verifyBackend(apiPort, token, nonce);
    await provisionMcp(apiPort, token);
    return view;
  } finally {
    settingsBusy = false;
  }
});
handle('desktop:getMcpSecrets', () => store.mcpView());
handle('desktop:saveMcpSecrets', async (server: string, values: Record<string, string>) => {
  if (!live) throw new Error('Backend is not running.');
  const { apiPort, token } = live;
  const auth = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
  if (await runsBusy(apiPort, auth)) throw new Error('Wait for the current operation to finish before changing MCP secrets.');
  const clean = store.checkMcpSecrets(server, values); // validate before anything is sent or stored
  const fingerprint = (await mcpFingerprints(apiPort, token))[server];
  if (!fingerprint) throw new Error('MCP server not found.');
  pm.addRedactions(Object.values(clean)); // never let a value reach a service log, whatever happens next
  // Backend first (in memory, no restart): it also checks the server exists and the secret name is valid.
  const r = await fetch(`http://127.0.0.1:${apiPort}/mcp-servers/${encodeURIComponent(server)}/secrets`, { method: 'POST', headers: auth, body: JSON.stringify(clean) });
  if (!r.ok) throw new Error(((await r.json().catch(() => ({}))) as { error?: string }).error || 'Backend rejected the secrets.');
  const view = store.saveMcpSecrets(server, clean, fingerprint); // only now persist to the keychain, bound to this target
  pm.addRedactions(store.mcpValues());
  return view;
});
handle('desktop:deleteMcpServer', async (server: string) => {
  if (!live) throw new Error('Backend is not running.');
  const { apiPort, token } = live;
  store.checkMcpSecrets(server, {}); // validates the name
  const auth = { Authorization: `Bearer ${token}` };
  if (await runsBusy(apiPort, auth)) throw new Error('Wait for the current operation to finish before deleting an MCP server.');
  const r = await fetch(`http://127.0.0.1:${apiPort}/mcp-servers/${encodeURIComponent(server)}`, { method: 'DELETE', headers: auth });
  if (!r.ok) throw new Error(((await r.json().catch(() => ({}))) as { error?: string }).error || 'Backend refused the delete.');
  store.removeMcpServer(server); // the keychain forgets its secrets only after the backend deleted the server
  return store.mcpView();
});
handle('desktop:checkForUpdates', () => shell.openExternal(RELEASES_URL));

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (win) { if (win.isMinimized()) win.restore(); win.show(); win.focus(); }
  });
  app.whenReady().then(async () => {
    buildMenu(app.getPath('userData'));
    createWindow();
    if (SMOKE) setTimeout(() => void smokeFail(`timed out after ${SMOKE_TIMEOUT_MS / 1000} s`), SMOKE_TIMEOUT_MS);
    try {
      const { apiPort, uiPort: uiPortNum } = await boot();
      if (SMOKE) {
        await smokeCheckConfig(uiPortNum);
        await smokeSucceed(apiPort, uiPortNum);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      if (SMOKE) {
        await smokeFail(message);
        return;
      }
      exitCode = 1;
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
    void pm.stopAll().finally(() => app.exit(exitCode));
  });
}
