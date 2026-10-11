# desktop/ (Electron shell)

Spawns the packaged Python backend and the standalone Next server on loopback, shows them in a locked-down window. Design and packaging: [../docs/DESKTOP.md](../docs/DESKTOP.md), release: [../docs/RELEASING.md](../docs/RELEASING.md).

## Key files
- `src/main.ts`: picks 2 free loopback ports, generates a 48-byte per-launch token, starts API + UI, creates the `BrowserWindow` (sandbox, contextIsolation, CSP, navigation guard).
- `src/process-manager.ts`: spawn/kill of both process trees (POSIX process-group SIGTERM then SIGKILL; Windows `taskkill /T /F`).
- `src/keystore.ts`: model/API key storage via Electron `safeStorage` into `<userData>/secrets.bin` (0600); in-memory fallback.
- `src/preload.ts`, `src/util.ts`: minimal bridge and port/token/URL-guard helpers. Tests: `src/util.test.ts`, `src/keystore.test.ts` (`node:test`).
- `electron-builder.yml`, `package.json`: targets (NSIS x64, dmg x64+arm64, AppImage x64), scripts `build`, `start`, `test`, `dist`.

## Contracts
Env passed to the backend: `HARNESS_API_TOKEN`, `HARNESS_API_PORT`, `HARNESS_DATA_DIR` (`app.getPath('userData')`), `HARNESS_RESOURCE_ROOT` (`<resources>/backend` when packaged); honoured by `src/maf_test_harness/paths.py`. Backend binary: `maf-backend` (`desktop_backend.py`, `packaging/backend.spec`).

## Invariants
- The token and API key never reach the renderer, logs or CLI args (logs redact the token).
- Windows/macOS/Linux behaviour must match; build each installer on its own OS runner.
- No auto-update; builds are unsigned (see DESKTOP.md for run instructions).

## Commands
```bash
cd desktop && npm ci && npm test        # tsc + node --test "dist/**/*.test.js"
npm start                               # dev shell (needs frontend built, see DESKTOP.md)
uv run python ../scripts/build_desktop.py --dry-run   # prints the packaging plan
```

- Smoke mode: `AGENTIC_QE_SMOKE=1` (set by `scripts/desktop_smoke.py`) uses a temp userData dir, prints one `AGENTIC_QE_SMOKE OK|FAIL` line, skips error dialogs and quits after a 150 s hard timeout. Keep that line format stable.

## Recipe: pass a new setting to the backend
Add it in `main.ts` env for the spawn (and in `keystore.ts` if secret), read it in `settings.py`, add a `node:test` for helpers in `util.test.ts`.
