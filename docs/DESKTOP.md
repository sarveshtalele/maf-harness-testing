# Desktop app (Electron)

## Architecture

```
Electron main (desktop/src/main.ts)
 ├─ picks 2 free loopback ports, generates a 48-byte per-launch token
 ├─ Python API   127.0.0.1:<api>   (HARNESS_API_TOKEN, HARNESS_API_PORT, HARNESS_DATA_DIR, HARNESS_RESOURCE_ROOT)
 ├─ Next standalone 127.0.0.1:<ui>  (same token/port env; /api/harness/* proxies to the API)
 └─ BrowserWindow -> http://127.0.0.1:<ui>   (sandbox, contextIsolation, no nodeIntegration, CSP, navigation guard)
```

The token never reaches the renderer; only the Next server process and the API hold it. On quit, both process trees are killed
(POSIX: process-group SIGTERM, SIGKILL after 5 s; Windows: `taskkill /T /F`). Logs: `<userData>/logs/desktop-*.log`
(token redacted). Data lives in `app.getPath('userData')` (`HARNESS_DATA_DIR`). Packaged builds set
`HARNESS_RESOURCE_ROOT=<resources>/backend`. `src/maf_test_harness/paths.py` honours both variables, and `desktop_backend.py` is the `maf-backend` entry point (`--self-check` verifies the bundled resources without network access).

## Run in development

```bash
uv sync --extra dev                      # repo .venv used by the shell
cd frontend && npm ci
DESKTOP_BUILD=1 npm run build            # produces .next/standalone (omit to fall back to `next start`)
cd ../desktop && npm ci && npm start     # tsc then electron .
npm test                                 # node:test unit tests for port/token/URL-guard helpers
```

Without `DESKTOP_BUILD=1` the frontend config is unchanged, so `scripts/start_ui.py` / `next start` keep working.
The dev shell prefers `frontend/.next/standalone/server.js` and falls back to `next start`. Standalone also needs
`.next/static` and `public` copied next to `server.js` (done by electron-builder `extraResources`; for dev standalone copy them manually
or just use the `next start` fallback by not setting `DESKTOP_BUILD`).

## Package

1. Backend: PyInstaller one-folder build with entry `maf-backend`, from `packaging/backend.spec`. `uv run python scripts/build_desktop.py --dry-run` prints the full plan without running it.
2. Frontend: `cd frontend && DESKTOP_BUILD=1 npm run build`.
3. `cd desktop && npm ci && npm run dist` (or `dist:win|mac|linux`; each OS must be built on its own runner). Output in `desktop/release/`.

CI builds installers in `.github/workflows/desktop-prerelease.yml` (manual run, or a `v*-rc*` tag) and `desktop-release.yml` (a `v*.*.*` tag, which creates a draft GitHub Release). Both run `scripts/check_version.py`, build the backend for each matrix entry, and run `maf-backend --self-check`. The matrix is Windows, Ubuntu, macOS arm64 on `macos-14`, and macOS x64 on `macos-15-intel`. Each arch is built on its own runner, so the bundled backend matches the runner's architecture. See [RELEASING.md](RELEASING.md).

The desktop unit tests (`npm ci && npm test` in `desktop/`) run in `ui.yml` on macOS, Windows and Ubuntu.

## Packaged-app smoke test

`uv run python scripts/desktop_smoke.py` installs or mounts the installer in `desktop/release/` (or `--installer PATH`) and
launches the packaged app with `AGENTIC_QE_SMOKE=1` in a fresh temp profile. The app boots the backend and UI, checks
`/api/harness/config` through the UI port, prints `AGENTIC_QE_SMOKE OK backend=<port> ui=<port>` and quits (exit 0). Failures
print `AGENTIC_QE_SMOKE FAIL <reason>`, the last 40 lines of each service log, and exit 1. Smoke mode also has a 150 s hard
timeout and shows no error dialogs. Windows installs silently into a temp dir (or the per-user folder if `/D` is ignored),
macOS mounts the DMG and runs the copied app, and Linux runs the AppImage with `--appimage-extract-and-run` (needs a display;
CI uses `xvfb-run -a`). The script refuses to run on Windows if a per-user Agentic QE install already exists. The
`desktop-prerelease` and `desktop-release` workflows run it after the backend self-check and upload `smoke-output.txt` as
`smoke-desktop-<os>` when it fails.

Targets: Windows NSIS x64, macOS dmg x64+arm64, Linux AppImage x64. No auto-update: Help > "Check for updates" opens the
GitHub Releases page (`homepage` in `desktop/package.json`).

## Unsigned builds (internal distribution)

- **macOS**: unsigned/not notarized. After download run `xattr -dr com.apple.quarantine "/Applications/Agentic QE.app"`, or right-click > Open once.
- **Windows**: SmartScreen shows "Windows protected your PC"; choose More info > Run anyway.
- **Linux**: `chmod +x Agentic-QE-*.AppImage` then run it (needs FUSE 2, or `--appimage-extract-and-run`).

## Model and API key

Set in the app under Configuration, Model & API key. Resolution order per field (`settings.current()`): runtime override
(entered in the UI) > environment > `.env`. In the desktop app the key, base URL, model and per-stage models are encrypted
with Electron `safeStorage` into `<userData>/secrets.bin` (0600), passed to the backend as `OPENAI_*` / `HARNESS_STAGE_MODELS`
at spawn, and the backend restarts when they change. Without OS encryption they stay in memory for the session. In a browser
the key lives in the running local server only; "Save to .env" (developer) writes it to the `.env` file (0600).
