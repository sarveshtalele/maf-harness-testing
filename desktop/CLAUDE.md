# desktop/ (Electron shell)

Spawns the packaged Python backend and the standalone Next server on loopback, shows them in a locked-down window. Design and packaging: [../docs/DESKTOP.md](../docs/DESKTOP.md), release: [../docs/RELEASING.md](../docs/RELEASING.md).

## Key files
- `src/main.ts`: picks 2 free loopback ports, generates a 48-byte per-launch token, a one-time launch secret and a per-launch nonce; the backend's ready line must echo the nonce before anything else is sent. The window gets an HttpOnly session cookie from the launch secret (`exchangeLaunchSecret`) before its first load. IPC is accepted only from the app's own page. Creates the `BrowserWindow` (sandbox, contextIsolation, frame-navigation guard, all permissions denied). CSP comes from the UI (`frontend/proxy.ts`).
- `src/process-manager.ts`: spawn/kill of both process trees (POSIX: SIGTERM to the group, poll until empty, SIGKILL; Windows: `taskkill /T /F` at once). Stdin of each service is a pipe (exit on EOF). Records live pids in `<userData>/children.json`; `reapOrphans` stops only those whose command line still has the record's marker (UI: the preload path; backend: the per-launch `--instance` id). Readiness: the backend's own `Local harness API: http://127.0.0.1:<port>` line.
- `src/keystore.ts`: model/API key storage via Electron `safeStorage` into `<userData>/secrets.bin` (0600); in-memory fallback. A base-URL host change needs a new key in the same save. Each MCP secret set is stored with the target fingerprint from `GET /mcp-servers`; `reconcileMcp` drops sets whose target changed, and the rest reach the backend by POST after boot (never in the spawn env).
- `src/preload.ts`, `src/util.ts`: minimal bridge and port/token/URL-guard/session helpers. Tests: `src/util.test.ts`, `src/keystore.test.ts` (`node:test`). `tests/test_desktop_hardening.py` checks the fuses and the IPC guard, which need an Electron window to test directly.
- `electron-builder.yml`, `package.json`: targets (Windows MSI x64 per-machine with WiX, dmg x64+arm64, AppImage x64), scripts `build`, `start`, `test`, `dist`. The MSI `upgradeCode` is fixed and must never change.

## Contracts
Env passed to the backend: `HARNESS_API_TOKEN`, `HARNESS_API_PORT`, `HARNESS_DATA_DIR` (`app.getPath('userData')`), `HARNESS_RESOURCE_ROOT` (`<resources>/backend` when packaged); honoured by `src/maf_test_harness/paths.py`. Backend binary: `maf-backend` (`desktop_backend.py`, `packaging/backend.spec`).

## Invariants
- The token and API key never reach the renderer, logs or CLI args (logs redact the token).
- Windows/macOS/Linux behaviour must match; build each installer on its own OS runner.
- No auto-update; builds are unsigned (see DESKTOP.md for run instructions).
- The Windows install is per machine in Program Files, which standard users cannot write. The app and backend must write only under user data (`app.getPath('userData')` / `HARNESS_DATA_DIR`), never under `resources/` (`HARNESS_RESOURCE_ROOT`). Tested by `tests/test_read_only_resources.py`.

## Commands
```bash
cd desktop && npm ci && npm test        # tsc + node --test "dist/**/*.test.js"
npm start                               # dev shell (needs frontend built, see DESKTOP.md)
uv run python ../scripts/build_desktop.py --dry-run   # prints the packaging plan
```

- Smoke mode: `AGENTIC_QE_SMOKE=1` (set by `scripts/desktop_smoke.py`) uses a temp userData dir, prints one `AGENTIC_QE_SMOKE OK|FAIL` line, skips error dialogs and quits after a 150 s hard timeout. Keep that line format stable.

## Recipe: pass a new setting to the backend
Add it in `main.ts` env for the spawn (and in `keystore.ts` if secret), read it in `settings.py`, add a `node:test` for helpers in `util.test.ts`.
