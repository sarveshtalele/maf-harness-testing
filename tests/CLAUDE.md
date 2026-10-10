# tests/ (pytest suite)

Layers: unit, component (real MAF with a scripted transport) and workflow end-to-end. Browser journeys live in `frontend/tests`, desktop tests in `desktop/src/*.test.ts`.

## Shared helpers
- `fakes.py`: `ScriptedStageRuntime` (schema-valid stage artifacts for the tiny `service.add` repo; replaces only the model call; has `run_stage`, `run_tool_agent`), `clone_fixture` (stands in for `clone_repository`), `review_response`, `design_review_response`, `local_env_plan`.
- `conftest.py`: autouse fixtures redirect the token ledger (`HARNESS_USAGE_DIR`) and stub hook config paths so developer files never leak in.
- `ui_fixture_server.py`: real `LocalAPI` + workflow with scripted model and fixture clone, for Playwright. `error_cases.py`: sample tool output per error code. `targets/`: opt-in acceptance tests against disposable deployed apps (e.g. `PETSTORE_BASE_URL`); excluded from default runs.

## Conventions
- Name by area: `test_<module>.py`; one behaviour per test; prefer `tmp_path`; no network, no real model, no sleeping on real time.
- Monkeypatch at the module that uses the name (e.g. `wf.clone_repository = clone_fixture`).
- Cross-OS: build paths with `pathlib`, no POSIX-only assumptions, skip with a reason when an OS tool is absent.
- `test_architecture_map.py` fails when `docs/ARCHITECTURE_MAP.md` is stale; `test_module_layout.py` imports every module.

## Commands
```bash
uv run pytest -q --ignore=tests/targets                 # full suite
uv run pytest -q tests/test_triage.py -k remedy          # focused
.venv/bin/python -m pytest -q --ignore=tests/targets    # without uv
uv run ruff check src tests scripts && uv run ruff format --check src tests scripts
```
Sandboxed agent shells need the sandbox off for tests that bind ports (launcher, Playwright).

## Recipe: workflow test
`monkeypatch.setattr(wf, "clone_repository", clone_fixture)`, build `Harness(tmp_path, skills_root=Path("skills"), runtime=ScriptedStageRuntime(), execute=False)`, then drive `RuntimeService().submit(h.start(url))` / `h.respond(h.view["pending"]["id"], {"decision": "approve"})` through the gates (see `test_harness_gates.py`, `test_execution_stage.py`). To exercise a failing batch, pass `extra_cases` or raise from a scripted `run_stage`.
