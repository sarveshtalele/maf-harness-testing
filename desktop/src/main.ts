import { app, BrowserWindow, dialog, ipcMain, Menu, safeStorage, session, shell } from 'electron';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { KeyStore, ModelSettingsInput } from './keystore';
import { ProcessManager, ServiceSpec, waitForHttp } from './process-manager';
import { canOpenExternally, csp, isAppOrigin, makeToken, pickTwoPorts } from './util';

const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf8')) as {
  homepage: string;
};
const RELEASES_URL = `${pkg.homepage.replace(/\/$/, '')}/releases`;

let pm: ProcessManager;
let win: BrowserWindow | null = null;
let quitting = false;
let store: KeyStore;
let live: { backend: ServiceSpec; apiPort: number; token: string } | null = null;

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
        cwd: path.join(res, 'frontend'), env: uiEnv,
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

async function boot() {
  const dataDir = app.getPath('userData');
  pm = new ProcessManager(path.join(dataDir, 'logs'));
  store = new KeyStore(path.join(dataDir, 'secrets.bin'), safeStorage);
  const [apiPort, uiPort] = await pickTwoPorts();
  const token = makeToken();
  const { backend, ui } = specs(apiPort, uiPort, token);

  live = { backend, apiPort, token };
  const be = pm.start(backend, [token, store.get().api_key]);
  await waitForHttp(`http://127.0.0.1:${apiPort}/`, 200, 90_000, () => be.exitCode === null).catch(async () => {
    // The API may answer non-200 on "/" yet be up; fall back to a TCP-level check via any HTTP response.
    const r = await fetch(`http://127.0.0.1:${apiPort}/`).catch(() => null);
    if (!r) throw new Error('backend did not start; see logs in ' + path.join(dataDir, 'logs'));
  });
  const fe = pm.start(ui, [token]);
  await waitForHttp(`http://127.0.0.1:${uiPort}/`, 200, 90_000, () => fe.exitCode === null);
  await waitForHttp(`http://127.0.0.1:${uiPort}/api/harness/config`, 200, 30_000);

  const origin = `http://127.0.0.1:${uiPort}`;
  session.defaultSession.webRequest.onHeadersReceived((details, cb) => {
    const headers = { ...details.responseHeaders };
    if (details.url.startsWith(origin)) headers['Content-Security-Policy'] = [csp(uiPort)];
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
}

ipcMain.handle('desktop:appVersion', () => app.getVersion());
ipcMain.handle('desktop:openDataFolder', () => shell.openPath(app.getPath('userData')).then(() => undefined));
ipcMain.handle('desktop:getModelSettings', () => store.view());
ipcMain.handle('desktop:saveModelSettings', async (_e, input: ModelSettingsInput) => {
  if (!live) throw new Error('Backend is not running.');
  const { apiPort, token } = live;
  const runs = await fetch(`http://127.0.0.1:${apiPort}/runs`, { headers: { Authorization: `Bearer ${token}` } })
    .then((r) => r.json() as Promise<{ busy?: boolean }>).catch(() => ({ busy: false }));
  if (runs.busy) throw new Error('Wait for the current operation to finish before changing model settings.');
  const view = store.save(input); // validates before anything restarts
  const backend = specs(apiPort, 0, token).backend; // re-reads the new key into the spawn env
  live.backend = backend;
  const child = await pm.restart(backend, [token, store.get().api_key]);
  await waitForHttp(`http://127.0.0.1:${apiPort}/`, 200, 60_000, () => child.exitCode === null).catch(() => undefined);
  return view;
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
    try {
      await boot();
    } catch (err) {
      dialog.showErrorBox('Agentic QE failed to start', `${err instanceof Error ? err.message : err}\n\nLogs: ${path.join(app.getPath('userData'), 'logs')}`);
      app.quit();
    }
  });
  app.on('window-all-closed', () => app.quit());
  app.on('before-quit', (e) => {
    if (quitting || !pm) return;
    e.preventDefault();
    quitting = true;
    void pm.stopAll().finally(() => app.exit(0));
  });
}
