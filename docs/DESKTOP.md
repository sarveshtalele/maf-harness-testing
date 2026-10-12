# Desktop app (Electron)

## Architecture

```
Electron main (desktop/src/main.ts)
 ├─ picks 2 free loopback ports, generates a 48-byte per-launch token
 ├─ Python API   127.0.0.1:<api>   (HARNESS_API_TOKEN, HARNESS_API_PORT, HARNESS_DATA_DIR, HARNESS_RESOURCE_ROOT)
 ├─ Next standalone 127.0.0.1:<ui>  (same token/port env; /api/harness/* proxies to the API)
 └─ BrowserWindow -> http://127.0.0.1:<ui>   (sandbox, contextIsolation, no nodeIntegration, CSP, navigation guard)
```

The token never reaches the renderer; only the Next server process and the API hold it. Logs: `<userData>/logs/desktop-*.log`
(token redacted). Data lives in `app.getPath('userData')` (`HARNESS_DATA_DIR`). Packaged builds set
`HARNESS_RESOURCE_ROOT=<resources>/backend`. `src/maf_test_harness/paths.py` honours both variables, and `desktop_backend.py` is the `maf-backend` entry point (`--self-check` verifies the bundled resources without network access).

## Process lifecycle

- **Parent death.** Each service is spawned in its own process group (POSIX) with stdin as a pipe. The backend exits when its stdin reaches EOF (`HARNESS_EXIT_ON_STDIN_EOF=1`, set by the shell only). The Next server starts with a `--require` preload, written to `<userData>/parent-exit.cjs`, that exits on stdin end. When the shell dies, the pipes close and the services exit.
- **Orphans.** Each live service is recorded in `<userData>/children.json` (name, pid, port, marker). At the next start, a recorded process is stopped only when its command line still carries the recorded marker, so a reused pid is never touched.
- **Stop.** POSIX: SIGTERM to the process group, wait up to 5 s, then SIGKILL the group, even when its leader already exited. Windows: `taskkill /T`, wait 5 s, then `taskkill /T /F`. `taskkill /T` cannot reach grandchildren whose parent is already gone; the stdin pipes are what protects against that.
- **Readiness.** The backend counts as ready only when it prints its own bound port (`Local harness API: http://127.0.0.1:<port>`), so another process on that port is never given the token. The UI is checked with HTTP and exits fail the wait fast.
- **macOS.** A Finder or Dock launch gets launchd's minimal PATH. The services get the login shell's PATH (`$SHELL -ilc`, 3 s timeout), then the inherited PATH, then `/opt/homebrew/bin` and `/usr/local/bin`.

## Run in development

```bash
uv sync --locked --extra dev             # repo .venv, used by the shell
cd frontend && npm ci --include=dev && npm run build
cd ../desktop && npm ci && npm start     # tsc, then electron .
npm test                                 # node:test: helpers, process tree kill and orphan reaping
```

Do not set `DESKTOP_BUILD=1` for a dev run. That build emits `.next/standalone`, which needs `.next/static` and `public` copied next to `server.js`. Only `scripts/build_desktop.py` copies them, so a dev run would show a UI with missing assets. Without the variable the shell falls back to `next start`, which needs no copy.

## Package

1. Backend: PyInstaller one-folder build with entry `maf-backend`, from `packaging/backend.spec`. `uv run python scripts/build_desktop.py --dry-run` prints the full plan without running it. The build refuses an `--arch` that differs from the machine.
2. Frontend: `cd frontend && DESKTOP_BUILD=1 npm run build`; `build_desktop.py` then copies the static assets.
3. `cd desktop && npm ci && npm run dist` (or `dist:win|mac|linux`; each OS must be built on its own runner). Output in `desktop/release/`.

CI builds installers in `.github/workflows/desktop-prerelease.yml` (manual run, or a `v*-rc*` tag) and `desktop-release.yml` (a `v*.*.*` tag, which creates a draft GitHub Release after `ci` and `ui` pass on the tag). Both run `scripts/check_version.py`, build the backend for each matrix entry, and run `maf-backend --self-check`. The matrix is Windows, Ubuntu, macOS arm64 on `macos-14`, and macOS x64 on `macos-15-intel`. Each arch is built on its own runner, so the bundled backend matches the runner's architecture. See [RELEASING.md](RELEASING.md).

The desktop unit tests (`npm ci && npm test` in `desktop/`) run in `ui.yml` on macOS, Windows and Ubuntu, and in `nightly.yml`.

## Packaged-app smoke test

`uv run python scripts/desktop_smoke.py` installs or mounts the installer in `desktop/release/` (or `--installer PATH`) and
launches the packaged app with `AGENTIC_QE_SMOKE=1` in a fresh temp profile. The app boots the backend and UI, checks
`/api/harness/config` through the UI port, prints `AGENTIC_QE_SMOKE OK backend=<port> ui=<port>` and quits (exit 0). Failures
print `AGENTIC_QE_SMOKE FAIL <reason>` and the last 40 lines of each service log, and exit 1. Smoke mode also has a 150 s hard
timeout, shows no error dialogs, and deletes its temp profile when it ends.

The run fails when any of these hold:

- the app prints no OK line, or exits with a non-zero code;
- a `maf-backend` or frontend `server.js` process is still running 10 s after the app exited (`pgrep`, or PowerShell on Windows);
- on Windows, the install folder is still there after uninstall, or `msiexec /x` exits with anything but 0. Exit code 3010 means a file was locked and the uninstall is incomplete.

Per OS: **Windows** does a silent per-machine MSI install into the default `Program Files\Agentic QE` (no folder override), write-protects that folder for this user while the app runs, restores its ACL, then runs a silent uninstall. It needs administrator rights, which the GitHub Windows runners have, and refuses to run if the folder already exists. **macOS** mounts the DMG and runs the copied app. **Linux** runs the AppImage with `--appimage-extract-and-run --no-sandbox` under `xvfb-run -a` in CI. The `--no-sandbox` flag is a CI requirement; it is not what users should run (see Linux below).

The `desktop-prerelease` and `desktop-release` workflows run it after the backend self-check and upload `smoke-output.txt` as `smoke-desktop-<os>` when it fails.

Targets: Windows MSI x64 (per-machine, WiX), macOS dmg x64+arm64, Linux AppImage x64. The MSI config lives in `desktop/electron-builder.yml`: `perMachine`, `oneClick: false` and a fixed `upgradeCode`, which must never change. electron-builder downloads its own WiX binaries on the Windows runner, so no extra CI step is needed. No auto-update: Help > "Check for updates" opens the GitHub Releases page (`homepage` in `desktop/package.json`).

## Unsigned builds (internal distribution)

- **macOS**: unsigned/not notarized, so macOS says *Apple could not verify "Agentic QE" is free of malware*. Drag the app to Applications, then either click **Open Anyway** in System Settings → Privacy & Security after the first blocked launch, or run `xattr -dr com.apple.quarantine "/Applications/Agentic QE.app"`. On macOS 15+ right-click > Open no longer bypasses Gatekeeper. Removing the prompt for everyone needs Developer ID signing + notarization (Apple Developer Program); see RELEASING.md.
- **Windows**: the MSI installs per machine into Program Files and needs administrator approval. SmartScreen may still show "Windows protected your PC" because the build is unsigned; choose More info > Run anyway. The MSI installs in Program Files, where standard users cannot modify it, and supports managed deployment (`msiexec /i <msi> /qn`). UAC shows "Unknown publisher" for the unsigned build; code signing is still what proves the publisher. The MSI blocks downgrades: to roll back, uninstall first ([USER_GUIDE.md](USER_GUIDE.md#roll-back-to-an-older-version)).
- **Linux**: `chmod +x Agentic-QE-*.AppImage` then run it (needs FUSE 2, or `--appimage-extract-and-run`). On Ubuntu 23.10 and later, AppArmor can block the unprivileged user namespaces that Electron's sandbox needs; the app then exits with a `chrome-sandbox` error. Either let an administrator allow the sandbox for the app with an AppArmor profile, or start it with `--no-sandbox`. `--no-sandbox` removes one isolation layer of the app's window, so use it only on a machine you trust. CI passes `--no-sandbox` too, so CI does not catch this failure.

## Model and API key

Set in the app under Configuration, Model & API key. Resolution order per field (`settings.current()`): runtime override
(entered in the UI) > environment > `.env`. In the desktop app the key, base URL, model and per-stage models are encrypted
with Electron `safeStorage` into `<userData>/secrets.bin` (0600), passed to the backend as `OPENAI_*` / `HARNESS_STAGE_MODELS`
at spawn, and the backend restarts when they change. Without OS encryption they stay in memory for the session. In a browser
the key lives in the running local server only; "Save to .env" (developer) writes it to the `.env` file (0600).
