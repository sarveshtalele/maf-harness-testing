# scripts/ (launchers and maintenance tools)

Run from the repo root with `uv run python scripts/<name>.py`. Keep them cross-OS (pathlib, UTF-8, argv lists, no `shell=True`).

## Files
- `start_ui.py`: starts the Python API (default 8765) and Next.js (default 3000) with a shared token; flags `--production`, `--prepare`, `--check-only`, `--smoke-test`, `--port`, `--api-port`. The launchers `start-windows.cmd`, `start-macos.command`, `start-linux.sh` are thin wrappers that call it. State file: `.maf-harness/launcher.json` (orphan cleanup, reuse of a verified running app).
- `gen_architecture.py`: regenerates `docs/ARCHITECTURE_MAP.md` (module table, import graphs, flowchart `FLOW`); `--check` exits 1 if stale. Never hand-edit the output.
- `vendor_skills.py`: vendors upstream skills and writes `skills/SKILLS.lock.json` (`--source <clone>`), or `--lock-project` to lock unlocked project-owned skills. Holds `SELECTED` and `REVIEWED_SCRIPTS`.
- `build_desktop.py`: `plan()` lists PyInstaller (`packaging/backend.spec`) -> `npm ci` -> `DESKTOP_BUILD=1 npm run build` -> `copy_static` -> electron-builder; `--dry-run` prints it. Build each OS on its own runner.
- `check_version.py`: `check(root)` fails if `pyproject.toml` and `frontend/package.json` versions differ or a tag mismatches (used by the desktop release workflow).
- `json_to_markdown.py`: convert a saved harness JSON artifact to Markdown (uses `ui/artifact_presenter.py`).
- `benchmark_generation.py`: offline scorer for generated pytest suites against `benchmarks/generation/` (baseline validity and seeded-defect kill score); no args runs reference calibration (CI), `--candidate-dir` scores submitted suites. See `docs/GENERATION_BENCHMARK.md`.

## Invariants
- `start_ui.py` requires Node 24; it invokes Next directly (no npm shims on Windows). It prints the saved setup/service log path.
- Adding or moving a `.py`/`.ts` file anywhere changes the architecture map: rerun `gen_architecture.py`.
- Version bumps touch `pyproject.toml` and `frontend/package.json` together (see `docs/RELEASING.md`).

## Test
`uv run pytest -q tests/test_architecture_map.py tests/test_check_version.py tests/test_desktop_build.py tests/test_ui_launcher.py`

## Recipe: add a script
Put it here with a module docstring (the map shows it), `main() -> int`, `if __name__ == "__main__": sys.exit(main())`; add a test in `tests/`; document it in the developer guide.
