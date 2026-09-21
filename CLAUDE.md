# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

MAF Test Harness: given a public GitHub repository URL, it clones and analyzes the repo, infers
requirements/actors/environment needs via an LLM agent (Microsoft Agent Framework + Agent Skills), installs
dependencies into an isolated environment (after approval), designs and generates a test suite in the target
repo's native framework, runs the tests, and returns a QA report + ZIP. Every model-authored artifact passes a
human approval gate; every action that touches the machine (clone, install, run) needs its own tool-call approval.

Full design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Every stage persona, central tool and Agent Skill:
[docs/CATALOG.md](docs/CATALOG.md). Roadmap: [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

## Commands

```bash
uv sync --extra dev                                          # install deps into .venv
uv run streamlit run app.py                                  # launch the UI at localhost:8501
uv run maf-harness run https://github.com/owner/repo         # CLI, interactive gates
uv run maf-harness run <repo> --auto-approve --auto-heal 2   # unattended; exit 0 pass / 1 fail / 2 inconclusive
uv run maf-harness run <repo> --run-dir .harness_runs/<run>  # resume an existing run at its pending gate
uv run maf-harness verify-run .harness_runs/<run>             # evidence the MAF harness + Agent Skills were really used
uv run maf-harness trace .harness_runs/<run>                  # skills/tools/cost/traceability report
uv run maf-harness lint-skills                                 # Agent Skills spec lint + lock verification

uv run ruff check src tests app.py scripts && uv run ruff format --check src tests app.py scripts
uv run pytest -q --ignore=tests/targets                       # unit + component (real MAF) + workflow e2e; no network, no model
uv run pytest tests/test_workflow.py -q                       # single test file
uv run pytest tests/test_workflow.py::test_name -q             # single test
HARNESS_BROWSER=1 PLAYWRIGHT_BROWSERS_PATH=$PWD/.browser-cache uv run pytest tests/targets/test_harness_browser.py -q   # UI in a real browser
```

`.env` (copy from `.env.example`) holds `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` for the model
gateway — git-ignored, read at startup, shell env wins over the file. Tests run without network or a model.

## Architecture

The harness is a **checkpointed MAF workflow** (`src/maf_test_harness/workflow.py`) of seven `Executor` stages,
driven identically by the Streamlit UI (`app.py`) and the CLI (`cli.py`). Human gates are MAF `request_info`
events; state checkpoints to disk (`FileCheckpointStorage`) under `.harness_runs/<run>/`.

| # | Stage | Actor | Human gate |
|---|---|---|---|
| 1 | `repo_analysis` | host (`repo_analyzer`, `stack`) | tool approval (clone) → review |
| 2 | `requirements` | agent + `repository-requirements` skill | review / revise |
| 3 | `environment_setup` | host (`environment`, `runners`) | **tool approval (install)** → review; can skip |
| 4 | `test_planning` | agent + `test-planning` skill | review / revise |
| 5 | `test_design` | agent + `test-design-generator`/`test-data-generator` | review / revise; generation blocked until approved |
| 6 | `test_generation` | agent + `generic-test-generator` (+ `junit-generator`, `playwright-test-generator`) | review / revise |
| 7 | `test_execution` | host (`runners`, `qa_report`, `triage`, `packaging`) | **tool approval (run)** → review; regenerate failing tests ≤3 rounds |

`execute=False` skips stages 3 and 7 (generation-only workflow).

**Central deterministic tools over model guessing** (`tools/code_intel.py`, `tools/autofix.py`, `tools/build_gate.py`,
`tools/agent_tools.py`): things the host can compute exactly — import resolution, class constructors, URL routes,
whether a file compiles — are answered by these instead of asking the model again. `tools/autofix.py` only applies
fixes that cannot change what a test means (unambiguous missing import, correcting a module path). Static gates
(imports, undefined names, mocked `sys.exit`, bad patch targets, unused setup, non-existent routes) always run;
`tools/build_gate.py` (`pytest --collect-only` / `mvn test-compile`) runs after the approved environment install.

**Generation pipeline** (stage 6, per batch): model call (temperature rises each retry) → normalize (canonical
case IDs, `CASE_ID` markers) → `autofix` → `skill_contracts.validate_generated_files` (structure/placeholders/
syntax/compile) → `code_intel.static_gate` → `build_gate.try_build`. Failures produce a concrete remedy
(`triage.remedy_for`) and loop back into the model; accepted batches are cached so retries/heals only regenerate
what failed.

**Agent Skills**: loaded through MAF's `SkillsProvider` (progressive disclosure), one skill assignment per
stage (table in `docs/ARCHITECTURE.md` §8). Vendored skills under `skills/` are pinned in
`skills/SKILLS.lock.json` (SHA-256 per file) — `lint-skills` fails if a vendored file was hand-edited; re-vendor
via `scripts/vendor_skills.py`. Only scripts recorded in the lock may execute
(`skill_scripts.HarnessScriptRunner`: allow-list, SHA-256, argv-only, temp dir, sanitized env, timeout).
`maf-harness verify-run` checks that every assigned skill was actually loaded via a tool-trace audit, not just
claimed.

**Budget**: a single daily token cap (`HARNESS_DAILY_TOKEN_LIMIT`, default 50,000,000) shared by all runs on the
machine — no per-run/per-call limits — enforced by `usage.DailyUsageLedger` + `maf_runtime.BudgetGuard`
(reserve → call → settle).

**Safety boundary**: credentials come only from env/`.env`, never forwarded to install/test commands
(`execution._child_env` is an allow-list with a fake `HOME`); install/run commands are argv-only with timeouts
and a `plan_hash` re-checked after approval; npm lifecycle scripts are disabled during install; execution is a
local process, not a sandboxed one — see `docs/ARCHITECTURE.md` §4 ("Guarantees and where they are enforced")
before changing anything in `envs/runners.py`, `envs/execution.py`, or `envs/environment.py`.

## Module map (`src/maf_test_harness/`)

Subpackages by concern (`repo/`, `envs/`, `skills/`, `tools/`, `agent/`, `llm/`, `qa/`, `monitoring/`, `ui/`, plus the
existing `errors/`); `workflow.py`, `cli.py`, `settings.py`, `models.py` stay at the top. The former flat module names
(`maf_test_harness.runners`, `.skill_contracts`, `.maf_runtime`, …) remain as aliases of the same module object, so old
imports and monkeypatches keep working; new code imports the subpackage path. Roughly in pipeline order:
- `workflow.py` — the MAF workflow graph, stage orchestration, prompts, repair loops (the core file)
- `repo/analyzer.py`, `repo/stack.py` — clone + static analysis, framework/stack detection
- `envs/environment.py`, `envs/runners.py`, `envs/execution.py` — environment planning, isolated install, command execution
- `envs/platform_compat.py` — every OS-specific decision (Windows vs POSIX venv layout, tool resolution, process-tree
  kill, fake-HOME isolation) centralized here; other modules never branch on `sys.platform` directly
- `skills/config.py`, `skills/registry.py`, `skills/contracts.py`, `skills/scripts.py`, `skills/reports.py` — Agent
  Skills loading, spec/lock validation, contract enforcement, sandboxed script execution
- `tools/code_intel.py`, `tools/autofix.py`, `tools/build_gate.py`, `tools/agent_tools.py` — the deterministic central tools
- `tools/registry.py`, `tools/env_tools.py`, `tools/file_tools.py`, `tools/typed.py` — typed tools: schema-validated
  arguments, one `ToolResult` envelope, hook-aware `ToolRegistry` (see [docs/HOOKS.md](docs/HOOKS.md))
- `hooks/` — hook events, the fail-closed verdict engine, built-in hooks (secret redaction, write scope, download
  consent, audit), `.harness/hooks.json` loader, MAF middleware bridge; `HARNESS_HOOKS=enforce|evaluate_only|off`
- `agent/runtime.py` — MAF agent construction, `SkillsProvider` wiring, tool-trace middleware, `BudgetGuard`
- `qa/report.py`, `qa/triage.py`, `qa/packaging.py` — verdict computation, files-to-update, ZIP assembly
- `monitoring/report.py`, `monitoring/ui.py`, `monitoring/trace.py`, `monitoring/telemetry.py` — run tracing and the Observability tab
- `llm/usage.py`, `settings.py`, `models.py`, `agent/personas.py`, `repo/routes.py` — token ledger, config, schemas, actor
  personas, app-route extraction
- `cli.py` — `maf-harness` entry point; `app.py` (repo root) — Streamlit UI, drives the same `workflow.py`

Tests mirror these modules 1:1 (`tests/test_<module>.py`); `tests/targets/` holds real target repos/apps used by
browser-driven (Playwright) tests, excluded from the default `pytest` run.
