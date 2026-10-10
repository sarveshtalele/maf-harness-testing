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
`HARNESS_RESOURCE_ROOT=<resources>/backend`. The backend honouring these two vars is `src/maf_test_harness/paths.py` (separate task).

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

1. Backend: PyInstaller one-folder build into `build/backend` with entry `maf-backend` (**pending, later task**).
2. Frontend: `cd frontend && DESKTOP_BUILD=1 npm run build`.
3. `cd desktop && npm ci && npm run dist` (or `dist:win|mac|linux`; each OS must be built on its own runner). Output in `desktop/release/`.

Targets: Windows NSIS x64, macOS dmg x64+arm64, Linux AppImage x64. No auto-update: Help > "Check for updates" opens the
GitHub Releases page (`homepage` in `desktop/package.json`; replace the placeholder with the real repo URL).

## Unsigned builds (internal distribution)

- **macOS**: unsigned/not notarized. After download run `xattr -dr com.apple.quarantine "/Applications/Agentic QE.app"`, or right-click > Open once.
- **Windows**: SmartScreen shows "Windows protected your PC"; choose More info > Run anyway.
- **Linux**: `chmod +x Agentic-QE-*.AppImage` then run it (needs FUSE 2, or `--appimage-extract-and-run`).
