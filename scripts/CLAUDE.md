# scripts/ (launchers and maintenance tools)

Run from the repo root with `uv run python scripts/<name>.py`. Keep them cross-OS (pathlib, UTF-8, argv lists, no `shell=True`).

## Files
- `start_ui.py`: starts the Python API (default 8765) and Next.js (default 3000) with a shared token; flags `--production`, `--prepare`, `--check-only`, `--smoke-test`, `--port`, `--api-port`. The launchers `start-windows.cmd`, `start-macos.command`, `start-linux.sh` are thin wrappers that call it. State file: `.maf-harness/launcher-<pid>.json`, one per launcher (orphan cleanup of dead launchers' files, reuse of a verified running app).
- `gen_architecture.py`: regenerates `docs/ARCHITECTURE_MAP.md` (module table, import graphs, flowchart `FLOW`); `--check` exits 1 if stale. Never hand-edit the output.
- `vendor_skills.py`: vendors upstream skills and writes `skills/SKILLS.lock.json` (`--source <clone>`), or `--lock-project` to lock unlocked project-owned skills. Holds `SELECTED` and `REVIEWED_SCRIPTS`.
- `build_desktop.py`: `plan()` lists PyInstaller (`packaging/backend.spec`) -> `npm ci` -> `DESKTOP_BUILD=1 npm run build` -> `copy_static` -> electron-builder; `--dry-run` prints it. Build each OS on its own runner.
- `check_version.py`: `check(root)` reads the six version files (pyproject, uv.lock, both package.json and both package-lock.json, root and `packages[""]`) and fails on any mismatch; a `GITHUB_REF_TYPE=tag` must be exactly `vX.Y.Z` or `vX.Y.Z-rcN` and match. Runs in CI and the desktop workflows.
- `check_all.py`: the merge gate in order (ruff, architecture map, version files, skills lint, pytest, frontend typecheck/build/Playwright, desktop tests). `--skip-ui`, `--skip-desktop`.
- `desktop_smoke.py`: installs or mounts the packaged desktop installer in `desktop/release/` and runs the app with `AGENTIC_QE_SMOKE=1`; passes only on the `AGENTIC_QE_SMOKE OK` line and exit 0 (Windows silent per-machine MSI install and uninstall with msiexec, macOS DMG, Linux AppImage under `xvfb-run`). Tests: `tests/test_desktop_smoke.py` (pure parts only, never launches).
- `json_to_markdown.py`: convert a saved harness JSON artifact to Markdown (uses `ui/artifact_presenter.py`).
- `benchmark_generation.py`: offline scorer for generated pytest suites against `benchmarks/generation/` (baseline validity and seeded-defect kill score); no args runs reference calibration (CI), `--candidate-dir` scores submitted suites. See `docs/GENERATION_BENCHMARK.md`.

## Invariants
- `start_ui.py` requires Node 24; it invokes Next directly (no npm shims on Windows). It prints the saved setup/service log path.
- Adding, moving or removing a `.py` file under `src/`, `scripts/` or `skills/`, or a `.ts` file under `frontend/app`, `frontend/components` or `frontend/lib` changes the architecture map: rerun `gen_architecture.py`.
- Version bumps touch all six version fields together (see `docs/RELEASING.md`); `check_version.py` verifies them.

## Test
`uv run pytest -q tests/test_architecture_map.py tests/test_check_version.py tests/test_desktop_build.py tests/test_ui_launcher.py`. The desktop tests run with `cd desktop && npm test`.

## Recipe: add a script
Put it here with a module docstring (the map shows it), `main() -> int`, `if __name__ == "__main__": sys.exit(main())`; add a test in `tests/`; document it in the developer guide.
