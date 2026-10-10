# maf_test_harness (Python package)

Checkpointed Microsoft Agent Framework (MAF) workflow that turns a repository (brownfield) or specs (greenfield) into executed, reviewed tests plus a QA report. Root rules: [../../CLAUDE.md](../../CLAUDE.md). Task guide: [../../docs/DEVELOPER_GUIDE.md](../../docs/DEVELOPER_GUIDE.md). Generated file map: [../../docs/ARCHITECTURE_MAP.md](../../docs/ARCHITECTURE_MAP.md).

## Entry points (pyproject scripts)
- `cli.py` `main`: `maf-harness run|verify-run|trace|lint-skills`; headless driver of the same workflow.
- `ui/api.py` `main` / `make_server` / `LocalAPI`: loopback HTTP API used by Next.js (`scripts/start_ui.py` starts it).
- `desktop_backend.py` `main` / `self_check`: bundled `maf-backend` for the Electron app.

## Top-level modules
- `workflow.py`: `Harness` (state, checkpoints, `ask`/`review`/`answer`/`enter`) and `StageExecutor` (one per stage; `request_info` gates).
- `workflow_support.py`: `STAGES`, `CORE_STAGES`, `ENV_STAGE`, `EXECUTION_STAGE`, `LANGUAGE_RULES`, `case_budget`, `friendly_error`.
- `workflow_steps/`: stage operations mixed into `Harness`. `settings.py`: env/`.env`/runtime settings (`current`, `set_runtime`).
- `models.py`: pydantic output schemas for model stages. `paths.py`: bundled-resource vs user-data roots (frozen/desktop aware).
- `run_options.py`: `parse_run_options` (mode brownfield/greenfield, unit/integration allowlist, `restrict_levels`).
- `storage.py`: `read_json` / `atomic_json` (Windows-lock tolerant; use these, not bare `open`).

## Subpackages (each has its own CLAUDE.md)
`agent/` model runtime, personas · `repo/` intake/analysis · `skills/` plugin registry/trust/host scripts · `envs/` isolated envs, runners, toolchains · `errors/` taxonomy+classifiers · `hooks/` fail-closed hook engine · `tools/` deterministic central tools · `qa/` reports, triage, ZIP · `llm/` prompt order, token ledger · `monitoring/` trace/report · `ui/` API services · `workflow_steps/`.

## Invariants
- Dependency direction: `workflow` -> `workflow_steps` -> {`agent`, `repo`, `envs`, `qa`, `tools`, `hooks`}; lower layers never import `workflow`. Check `docs/ARCHITECTURE_MAP.md` imported-by before moving code.
- Every machine action (clone, install, run) needs its approval gate; model artifacts need a review gate.
- Cross-OS: `pathlib`, `encoding="utf-8"`, no `shell=True`, OS branches only in `envs/platform_compat.py`.
- New files that import or are imported change the map: run `python scripts/gen_architecture.py` (CI runs `--check`).

## Test
`uv run pytest -q --ignore=tests/targets` (see [../../tests/CLAUDE.md](../../tests/CLAUDE.md)); `tests/test_module_layout.py` imports every module.
