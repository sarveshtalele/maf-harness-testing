# Repository assistant instructions

Shared guidance for coding assistants working in this repository. Follow the user's requested scope;
do not delegate work to other agents unless requested. The current `new-ui` application has a Next.js
frontend and a local Python API sharing the checkpointed backend with the CLI.

## What this is

MAF Test Harness: given a public GitHub repository URL, it clones and analyzes the repo, infers
requirements/actors/environment needs via an LLM agent (Microsoft Agent Framework + Agent Skills), installs
dependencies into an isolated environment (after approval), designs and generates a test suite in the target
repo's native framework, runs the tests, and returns a QA report + ZIP. Every model-authored artifact passes a
human approval gate; every action that touches the machine (clone, install, run) needs its own tool-call approval.

Full design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Every stage persona, central tool and Agent Skill:
[docs/CATALOG.md](docs/CATALOG.md). Roadmap: [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

## Setup and verification

```bash
uv sync --python 3.12 --extra dev --extra e2e
npm --prefix frontend ci
uv run python scripts/start_ui.py                          # Next.js :3000, Python API :8765
uv run maf-harness run https://github.com/owner/repo         # interactive approval gates
uv run maf-harness verify-run /path/to/run
uv run maf-harness trace /path/to/run
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run pytest -q --ignore=tests/targets
uv run maf-harness lint-skills
npm --prefix frontend run typecheck
npm --prefix frontend run build
uv run python scripts/benchmark_generation.py              # reference calibration, not model quality
```

From frontend, install Chromium with `npx playwright install chromium`, then run `npm test`.
Use focused checks while editing; complete the relevant regression gates before delivery.
For benchmark scoring semantics, run `uv run pytest tests/test_generation_benchmark.py -q`.
Changes to this offline corpus do not require live model calls, target installs or browser checks.
For generation/execution changes, include the affected regression/evaluation checks and report live
checks only when actually run. Live external repositories and installs require the user's scope to
authorize them. Never use --auto-approve as a development shortcut.
The optional hooks SDK needs `uv sync --extra dev --extra hooks` for its SDK tests.

.env supplies model gateway settings; shell variables override it. Do not read or print credentials.
Offline Python tests and the benchmark need no model or network after dependency installation.
UI runs default to ~/.maf-harness/runs; CLI runs default to .harness_runs. Use HARNESS_RUNS_DIR
when explicitly sharing them; never let two processes mutate the same active run.

## Guardrails (strict, do not weaken)

1. **Install every requirement, then proceed.** `environment_setup` must install every declared requirement and leave the
   installed set consistent (`pip check` clean) before *any* later stage starts (`HARNESS_STRICT_ENV`, default on). An incomplete
   environment is a blocking error gate (retry or stop), never a warning the reviewer can approve past; `Harness.enter` refuses to
   start a later stage while `state["environment"]["status"] == "incomplete"`. Do not add a way around it. Skipping the install is
   only ever the reviewer's explicit *Skip* decision.
2. **Fix the environment, not the tests.** When a pinned package cannot be installed on the current Python, the harness proposes
   another interpreter (`envs.recovery.python_fallback`); the download is listed in the next install plan and needs its own approval.
   Never loosen a repository pin, never install under a different name, never let generated tests stub a declared dependency to
   hide an environment failure.
3. **When a live target check is authorized:** install every requirement of the target first; do not start
   generation or report results from a half-installed environment. Do not claim synthetic checks are live evidence.
4. **Same rule for development:** `uv sync --extra dev` (and `--extra hooks` for the SDK tests) before running the gate.
5. **Static gates must know the real environment, not just the declared one.** Once `environment_setup` is `ready`, the installed
   set is guaranteed complete (rule 1), so `tools.code_intel.static_gate` trusts it: `check_python_imports` accepts an import that
   resolves against the packages actually installed (`tools.code_intel._installed_python_modules`), not only ones the repository
   pins directly — a declared package's transitive dependency (`langchain` -> `langchain_core`) is real and must not be rejected as
   "not a declared dependency" (that previously blocked the harness's own remedy for the mocked-LLM pitfall and produced the exact
   repeated failure this rule exists to stop). Every hint a static gate gives for a rejected pattern must name the concrete fix
   (e.g. `skills.contracts.check_python_test_hygiene` naming the constructor call to patch instead of a local variable it assigns),
   not a generic pointer — a vague hint is what makes the model guess again and hit the same gate on the next attempt.

## Architecture

The harness is a **checkpointed MAF workflow** (`src/maf_test_harness/workflow.py`) of seven `Executor` stages,
driven by the local API (`ui/api.py`) for Next.js and directly by the CLI (`cli.py`). Human gates are MAF `request_info`
events; state checkpoints to disk (`FileCheckpointStorage`) under the configured run directory.

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
case IDs, `CASE_ID` markers) → `autofix` → `skills.contracts.validate_generated_files` (structure/placeholders/
syntax/compile) → `tools.code_intel.static_gate` → `tools.build_gate.try_build`. Failures produce a concrete remedy
(`qa.triage.remedy_for`) and loop back into the model; accepted batches are cached so retries/heals only regenerate
what failed.

**Agent Skills**: loaded through MAF's `SkillsProvider` (progressive disclosure), one skill assignment per
stage (table in `docs/ARCHITECTURE.md` §8). Vendored skills under `skills/` are pinned in
`skills/SKILLS.lock.json` (SHA-256 per file) — `lint-skills` fails if a vendored file was hand-edited; re-vendor
via `scripts/vendor_skills.py`. Only scripts recorded in the lock may execute
(`skills.scripts.HarnessScriptRunner`: allow-list, SHA-256, argv-only, temp dir, sanitized env, timeout).
`maf-harness verify-run` checks that every assigned skill was actually loaded via a tool-trace audit, not just
claimed.

**Budget**: a single daily token cap (`HARNESS_DAILY_TOKEN_LIMIT`, default 50,000,000) shared by all runs on the
machine — no per-run/per-call limits — enforced by `llm.usage.DailyUsageLedger` + `agent.runtime.BudgetGuard`
(reserve → call → settle).

**Safety boundary**: credentials come only from env/`.env`, never forwarded to install/test commands
(`envs.execution._child_env` is an allow-list with a fake `HOME`); install/run commands are argv-only with timeouts
and a `plan_hash` re-checked after approval; npm lifecycle scripts are disabled during install; execution is a
local process, not a sandboxed one — see `docs/ARCHITECTURE.md` §4 ("Guarantees and where they are enforced")
before changing anything in `envs/runners.py`, `envs/execution.py`, or `envs/environment.py`.

## Module map (`src/maf_test_harness/`)

Subpackages by concern (`repo/`, `envs/`, `skills/`, `tools/`, `agent/`, `llm/`, `qa/`, `monitoring/`, `ui/`, plus the
existing `errors/`); `workflow.py`, `cli.py`, `settings.py`, `models.py` stay at the top. Every module import uses its
subpackage path (`maf_test_harness.envs.runners`, `.skills.contracts`, `.agent.runtime`, …) — the flat pre-refactor
names (`maf_test_harness.runners`, `.skill_contracts`, `.maf_runtime`) no longer exist; this is a private repo with no
external consumers of the old paths, so the compatibility shims that briefly bridged the Phase 1 subpackage refactor
were removed once every caller (all of them internal, in `tests/`) was updated to the new path. Roughly in pipeline
order:
- `workflow.py` — the MAF workflow graph, stage orchestration, prompts, repair loops (the core file)
- `repo/analyzer.py`, `repo/stack.py` — clone + static analysis, framework/stack detection
- `envs/environment.py`, `envs/runners.py`, `envs/execution.py` — environment planning, isolated install, command execution
- `envs/platform_compat.py` — every OS-specific decision (Windows vs POSIX venv layout, tool resolution, process-tree
  kill, fake-HOME isolation) centralized here; other modules never branch on `sys.platform` directly
- `skills/config.py`, `skills/registry.py`, `skills/contracts.py`, `skills/scripts.py`, `skills/reports.py` — Agent
  Skills loading, spec/lock validation, contract enforcement, restricted local script execution
- `tools/code_intel.py`, `tools/autofix.py`, `tools/build_gate.py`, `tools/agent_tools.py` — the deterministic central tools
- `tools/registry.py`, `tools/env_tools.py`, `tools/file_tools.py`, `tools/typed.py` — typed tools: schema-validated
  arguments, one `ToolResult` envelope, hook-aware `ToolRegistry` (see [docs/HOOKS.md](docs/HOOKS.md))
- `hooks/` — hook events, the fail-closed verdict engine, built-in hooks (secret redaction, write scope, download
  consent, audit), `.harness/hooks.json` loader, MAF middleware bridge, optional AGENT-HOOKS-0.1 backend;
  `HARNESS_HOOKS=enforce|evaluate_only|off`, `HARNESS_HOOKS_BACKEND=inhouse|sdk`
- `envs/recovery.py` — bounded automatic recovery of failed setup steps (allow-listed recipes, see
  [docs/AUTOMATION.md](docs/AUTOMATION.md)); `agent/diagnosis.py` — opt-in read-only diagnosis agent;
  `llm/prompts.py` — stable-first prompt assembly for the provider's prefix cache; `tools/skill_tools.py` — skill validators as a tool
- `agent/runtime.py` — MAF agent construction, `SkillsProvider` wiring, tool-trace middleware, `BudgetGuard`
- `qa/report.py`, `qa/triage.py`, `qa/packaging.py` — verdict computation, files-to-update, ZIP assembly
- `monitoring/report.py`, `monitoring/trace.py`, `monitoring/telemetry.py` — run tracing and the Observability tab
- `llm/usage.py`, `settings.py`, `models.py`, `agent/personas.py`, `repo/routes.py` — token ledger, config, schemas, actor
  personas, app-route extraction
- `cli.py` — maf-harness entry point
- `ui/api.py`, `ui/runtime_service.py` — authenticated loopback API and background runtime
- `frontend/app/api/harness/[...path]/route.ts` — local Next.js proxy; the API token stays server-side
- `frontend/components/` — workspace, prompt editing, reports, skills and observability
- `scripts/start_ui.py` — starts both services; platform launchers install/build and offer smoke checks

Tests mirror these modules 1:1 (`tests/test_<module>.py`); `tests/targets/` holds real target repos/apps used by
browser-driven (Playwright) tests, excluded from the default `pytest` run.

## Generation quality and evidence

Keep the host authoritative for approvals, execution and QA verdicts. Tests must use source-defined
schemas, signatures, payloads and selectors. Use disposable data, unique fixture identities and reliable
teardown; check combined suites for shared import-time state. Never patch target production code, mock
away required dependencies, skip prerequisites or relax meaningful assertions to obtain a green report.
Separate application defects, generated-test defects, environment failures and missing coverage.
Collection and passing generated tests do not prove correctness or complete coverage.

The offline corpus is under benchmarks/generation; scoring is in scripts/benchmark_generation.py.
Read [the benchmark guide](docs/GENERATION_BENCHMARK.md). Keep reference suites and seeded mutations
out of generation inputs. Preserve submitted suites and raw execution evidence. Baseline validity and
defect detection are separate measurements. Never label reference calibration as model quality or
supplemental hand-authored checks as generated QA.

Keep changes scoped and preserve checkpoint/approval semantics. Do not edit historical audits to imply
newer results. Deployment, publishing, external target changes and unattended installs need authorization
within the user's scope. Documentation uses scoped verification; backend changes need relevant regression
checks and UI changes need type/build/browser checks.
