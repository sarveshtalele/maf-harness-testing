# Developer guide

Start here if you will change code. It covers setup, the repository map, the branch and commit rules, naming, coding and testing standards, the merge gate, review, the how-to recipes and troubleshooting.

Rules that must never be broken are in [CLAUDE.md](../CLAUDE.md). Contribution steps are in [CONTRIBUTING.md](../CONTRIBUTING.md). Every source directory also has a short `CLAUDE.md` (purpose, key files, contracts, gotchas, test commands, change recipes) that Claude Code loads when it works there. Humans should read the one for the area they change.

Where a rule in this guide is not yet met by the code, it says so in [section 5.10](#510-known-gaps-between-rules-and-code). Do not treat those rules as enforced until the gap is closed.

## Contents

- [0. Setup and running locally](#0-setup-and-running-locally)
- [1. Repository map](#1-repository-map)
- [2. Branching model](#2-branching-model)
- [3. Commits](#3-commits)
- [4. Naming conventions](#4-naming-conventions)
- [5. Coding standards and principles](#5-coding-standards-and-principles)
- [6. Testing standards](#6-testing-standards)
- [7. The merge gate](#7-the-merge-gate)
- [8. Review process](#8-review-process)
- [9. How-to recipes](#9-how-to-recipes)
- [10. Troubleshooting for developers](#10-troubleshooting-for-developers)
- [Appendix: glossary](#appendix-glossary)

## 0. Setup and running locally

### 0.1 Prerequisites

- [Git](https://git-scm.com/).
- [Node.js 24](https://nodejs.org/). Exactly major 24 is required: `frontend/package.json` `engines` and `frontend/.npmrc` (`engine-strict=true`) refuse other versions. `frontend/.nvmrc` says `24`. `desktop/.npmrc` also sets `engine-strict`.
- [uv](https://docs.astral.sh/uv/getting-started/installation/). uv provisions Python 3.12 for the project. The package itself requires Python `>=3.11`.
- For the target repositories you test (not needed to develop the harness): their toolchains, such as a JDK with Maven or Gradle, or the .NET SDK. See [ENVIRONMENTS.md](ENVIRONMENTS.md).

### 0.2 Set up a checkout

macOS and Linux:

```bash
git clone https://github.com/sarveshtalele/maf-harness-testing.git
cd maf-harness-testing
git checkout develop
uv sync --python 3.12 --extra dev --extra e2e
cd frontend && npm ci --include=dev && cd ..
cd desktop && npm ci && cd ..
cp .env.example .env
chmod 600 .env
```

Windows (PowerShell):

```powershell
git clone https://github.com/sarveshtalele/maf-harness-testing.git
cd maf-harness-testing
git checkout develop
uv sync --python 3.12 --extra dev --extra e2e
cd frontend; npm ci --include=dev; cd ..
cd desktop; npm ci; cd ..
Copy-Item .env.example .env
```

Notes:

- `--extra e2e` installs Python Playwright for the browser tests. `--extra dev` installs pytest, ruff and mcp. CI uses `uv sync --locked`.
- Edit `.env` to set `OPENAI_API_KEY`, `OPENAI_BASE_URL` and `OPENAI_MODEL`. It is git-ignored; never commit it.
- `.env` is read from one place only, found from the code and never from the current directory (`paths.env_file()`): `<repository root>/.env` in a source checkout, and `<user data folder>/.env` in the packaged desktop app. Only `OPENAI_*`, `LLM_MODEL` and `HARNESS_*` keys are read from it. A variable already set in the shell wins.
- The key can also be set later in the UI (Configuration page). See [DESKTOP.md](DESKTOP.md) for precedence.

### 0.3 Run the API and the UI in dev mode

```bash
uv run python scripts/start_ui.py
```

This starts the Python API on `127.0.0.1:8765` and the Next.js dev server on `127.0.0.1:3000`. Open http://127.0.0.1:3000. Ctrl+C in the same terminal stops both. The `npm ci` in `frontend/` must have run first.

| Flag | Effect |
|---|---|
| `--port N` | UI port. Default 3000. Without an explicit value the launcher tries the next free ports (up to 10). |
| `--api-port N` | API port. Default 8765, with the same recovery. |
| `--check-only` | Checks Node 24 and the ports, then exits. Exit code 10 means this checkout's app is already running. |
| `--prepare` | Runs `npm ci --include=dev` and the production UI build. |
| `--production` | Serves the built UI with `next start`. Run `--prepare` first. |
| `--smoke-test` | Starts both services, checks the home page and the authenticated backend proxy, then stops. |

Launcher logs are in `.maf-harness/logs/launch-*.log`.

To run only the API, set a token of at least 32 characters:

```bash
HARNESS_API_TOKEN=<token-of-32-or-more-characters> uv run python -m maf_test_harness.ui.api --port 8765
```

### 0.4 Run from the command line

The CLI runs the same workflow as the UI, without a browser. It reads `.env` from the repository root.

```bash
uv run maf-harness run https://github.com/owner/repo
uv run maf-harness run https://github.com/owner/repo --no-execution
uv run maf-harness run https://github.com/owner/repo --auto-approve
uv run maf-harness trace .maf-harness/runs/<run-id>
uv run maf-harness verify-run .maf-harness/runs/<run-id>
uv run maf-harness lint-skills --strict
```

- Without `--auto-approve` every gate waits for an answer in the terminal.
- `--auto-approve` is explicit consent to clone the repository, install its dependencies and run its generated tests on this machine.
- `--no-execution` stops after generation. `--skip-execution` approves the gates but does not install or run tests.
- Ctrl+C stops the run and exits with code 130. Exit codes: 0 PASS or PASS_WITH_SKIPS, 1 FAIL, 2 inconclusive or workflow error.
- Runs go to `<repo-root>/.maf-harness/runs/<run-id>`. Override with `HARNESS_RUNS_DIR`.
- Live runs spend tokens. The daily ledger cap is `HARNESS_DAILY_TOKEN_LIMIT`. See [CACHING.md](CACHING.md).

### 0.5 Run the desktop shell in dev mode

```bash
cd frontend && npm run build && cd ..    # no DESKTOP_BUILD: the shell falls back to `next start`
cd desktop && npm start                  # tsc, then electron .
```

Do not set `DESKTOP_BUILD=1` for a dev run. Its standalone output needs static files that only `scripts/build_desktop.py` copies. Packaging is in [section 9](#9-how-to-recipes) and [DESKTOP.md](DESKTOP.md).

## 1. Repository map

### 1.1 Components and owners

Owner for every component is `@sarveshtalele`. That is the default owner in `.github/CODEOWNERS`, and it is also the only owner listed there. `skills/` and `.github/` are named explicitly as supply-chain sensitive.

**`src/maf_test_harness/` root modules.** The Python backend package (`pyproject.toml` name `maf-test-harness`). The root holds the checkpointed workflow entry (`workflow.py`), shared stage policy (`workflow_support.py`), the CLI (`cli.py`), the desktop backend entry (`desktop_backend.py`, `maf-backend`), settings loading (`settings.py`), path resolution for source and frozen builds (`paths.py`), run options (`run_options.py`), MCP server configuration (`mcp_servers.py`) and the data models (`models.py`). Read `src/maf_test_harness/CLAUDE.md` before you change any of them.

**`workflow_steps/`.** The operations for each stage, mixed into `Harness`: planning, design (batched by component), generation (batched generate, static gates, build gate, independent review), test review, execution, rerun and greenfield intake. A stage operation calls the model through `agent/`, validates the result against a schema in `models.py`, and then waits at a human gate.

**`agent/`.** The Microsoft Agent Framework runtime (`runtime.py`): per-stage agents, middleware, and the skills provider. It also holds the personas (`personas.py`, including the three confidence reviewers), the user-editable prompt settings (`prompt_settings.py`) and the diagnosis agent.

**`repo/`.** Intake of the target repository: clone (`git_transport.py`), static analysis (`analyzer.py`), stack detection (`stack.py`), routes, requirements, testability, import recipes, the preview check and greenfield specs (`specs.py`). Repository content is untrusted data; this package reads it, it does not run it.

**`skills/` (loader code).** The skill registry, the stage-to-skill configuration, the contracts that validate skill output, the host script runner, reports and trust (`skills/config.py`, `registry.py`, `contracts.py`, `scripts.py`, `host.py`, `reports.py`, `trust.py`). This package loads skills; the skill content is in the repo-root `skills/` folder below.

**`envs/`.** Isolated environments for the target repository: the Python environment (`python_env.py`) and the environment spec (`spec.py`), toolchain detection and provisioning for Python, Java and .NET (`toolchains.py`), runners that build the test command and parse JUnit (`runners.py`, `execution.py`), bounded automatic recovery with an allow-list (`recovery.py`), and `platform_compat.py`, which holds cancellation and process-tree helpers. Also `environment.py`, `env_check.py`, `setup_guide.py`, `app_launcher.py` and `regression.py`.

**`errors/`.** The error taxonomy (`taxonomy.py`), classifiers per ecosystem (`classifiers/`), and the recovery playbook (`playbook.py`). Every classified error has a stable code, a category, an autonomy level and a remedy.

**`tools/`.** Deterministic functions agents may call (central tools and the typed tool registry) and the build gate that collects generated tests. Facts the host can compute (imports, signatures, routes, compilation) belong here, not in prompts.

**`hooks/`.** The hook engine. Checks run before and after tool and model calls. It fails closed: a crashing hook stops the action.

**`qa/`.** The QA report, triage and remedies (`triage.py`, `REMEDIES`), step reports, the deliverable ZIP (`packaging.py`), the confidence scoring (`confidence.py`), the control IDs (`controls.py`) and the confidence ledger.

**`llm/`.** Prompt assembly: the section order lists in `prompts.py` (kept stable so the provider prefix cache keeps hitting) and the daily token ledger.

**`monitoring/`.** The `trace.jsonl` recorder, the observability report (`maf-harness trace` uses it) and OpenTelemetry setup (`telemetry.py`).

**`ui/`.** The loopback HTTP API (`api.py`, class `LocalAPI`, composed from `EvidenceAPI`, `SettingsAPI`, `McpAPI` and `ConfidenceAPI`), the runtime service that runs the workflow on one event loop (`runtime_service`), ETA and case budget (`eta.py`), the Errors tab (`errors.py`), and presentation (`presentation.py`, which redacts before anything reaches the browser).

**`frontend/`.** Next.js 16 and React 19 on Node 24. `app/` holds the App Router pages and the same-origin proxy to the Python API (`app/api/harness/[...path]/route.ts`). `components/` holds the UI, with the shared page template in `components/page.tsx`. `lib/` holds the API client and report parsing. `tests/` holds the Playwright browser journeys.

**`desktop/`.** The Electron shell, branded "Agentic QE". `src/main.ts` picks two free loopback ports, starts the backend and the UI, and creates the window. `process-manager.ts` starts and stops both process trees. `keystore.ts` stores secrets with Electron `safeStorage`. `preload.ts` is the only bridge to the renderer. `util.ts` holds pure helpers with `node:test` tests next to them. `electron-builder.yml` holds the installer targets.

**`scripts/`.** Maintenance and launch tools, run as `uv run python scripts/<name>.py`: `check_all.py` (the merge gate), `start_ui.py` (the launcher behind the three thin launchers at the repository root), `gen_architecture.py`, `check_version.py`, `vendor_skills.py` (writes the skill lock), `build_desktop.py`, `desktop_smoke.py`, `secrets_scan.py`, `benchmark_generation.py` and `json_to_markdown.py`.

**`packaging/`.** `backend.spec`, the PyInstaller spec for the desktop backend. Installer targets are in `desktop/electron-builder.yml`.

**`skills/` (packages).** Agent Skill packages (one folder each, `SKILL.md` plus `plugin.yaml`, optional `scripts/`, `assets/`, `references/`) and `SKILLS.lock.json`, which hash-locks every file. Stage and role rules are in `skills/CLAUDE.md`.

**`tests/`.** The pytest suite (about 100 `test_*.py` files), with shared helpers `fakes.py`, `conftest.py`, `ui_fixture_server.py` and `error_cases.py`. `tests/targets/` holds opt-in acceptance tests against deployed apps. Read `tests/CLAUDE.md` before you add a test.

**Other top-level folders.** `prompts/` holds the persona and reviewer prompt text shipped with the app. `benchmarks/generation/` is the reference corpus for the offline generation scorer. `docs/` is the documentation set; its index is [docs/README.md](README.md). `.github/` holds the workflows, CODEOWNERS and the PR template.

### 1.2 Layout

```text
.
├── CLAUDE.md, AGENTS.md, CONTRIBUTING.md   rules and entry points
├── README.md                    developer README on develop; on main, the user guide
├── pyproject.toml, uv.lock      Python package maf-test-harness (scripts: maf-harness, maf-backend)
├── start-{windows.cmd,macos.command,linux.sh}   thin launchers -> scripts/start_ui.py
├── src/maf_test_harness/        Python backend; each subpackage has a CLAUDE.md
├── frontend/                    Next.js UI, same-origin proxy, Playwright specs
├── desktop/                     Electron shell and node:test tests
├── skills/                      Agent Skill packages and SKILLS.lock.json
├── scripts/                     maintenance and launch tools
├── packaging/backend.spec       PyInstaller spec
├── tests/                       pytest suite
├── prompts/, benchmarks/        shipped prompt text; generation benchmark corpus
├── docs/                        documentation (index: docs/README.md)
├── .github/                     workflows, CODEOWNERS, PR template
└── memory.md                    project log (orchestrator-owned)
```

### 1.3 How a run flows

Stages (`workflow_support.STAGES`): `repo_analysis`, `requirements`, `test_planning`, `test_design`, `test_generation`, `environment_setup`, `test_execution`. The first five are `CORE_STAGES`. Each stage is a Microsoft Agent Framework executor and ends at a human gate (`Harness.ask` raises a `request_info` event). Greenfield runs replace repository analysis with `workflow_steps/greenfield.py`.

- Gates: `Harness.enter` decides what a stage does on entry. `Harness.review` stores `artifacts[stage]` and `{stage}-{revision}.json`. `Harness.answer` handles approve, revise, skip and stop.
- Model stages: `PlanningSteps.model` builds the prompt, calls `MAFHarnessRuntime.run_stage`, validates the result against a `models.py` schema, then opens a review gate.
- Workflow version 2: nothing is created in the target workspace, nothing is installed and no runtime collection happens before `environment_setup` is approved. Older runs keep their saved stage order.
- Stop: the UI Stop sets status `stopping`, sets the cancel event, cancels the task, raises `MiddlewareFailure` and kills the process tree. CLI Ctrl+C exits 130. Resume reruns from the active stage (`workflow_steps/rerun.py`); old evidence moves to `attempts/<id>`.
- Generation gate order and coverage accounting are in [CLAUDE.md](../CLAUDE.md), sections "Generation gate order" and "Coverage accounting".

The architecture is described in [ARCHITECTURE.md](ARCHITECTURE.md). The generated module table with imports and importers is [ARCHITECTURE_MAP.md](ARCHITECTURE_MAP.md). Never edit the map by hand.

### 1.4 The per-directory CLAUDE.md files

The root `CLAUDE.md` holds project rules. The areas have their own: `frontend/`, `desktop/`, `scripts/`, `skills/`, `tests/`, `src/maf_test_harness/`, and one per subpackage under it (`agent/`, `envs/`, `errors/`, `hooks/`, `llm/`, `monitoring/`, `qa/`, `repo/`, `skills/`, `tools/`, `ui/`, `workflow_steps/`). Each lists the key files, contracts, gotchas, test commands and change recipes for its area. When you change behaviour in an area, update its `CLAUDE.md` in the same commit.

## 2. Branching model

- **`develop`** is the integration branch and the default branch. Feature work merges here.
- **`main`** holds released code only. Its `README.md` is the user guide. When you merge `develop` into `main`, keep main's `README.md` (`git checkout --ours README.md`) and copy user-guide changes from `docs/USER_GUIDE.md`.
- **One branch per component change.** Name it `<type>/<component>-<short-topic>`, lowercase, words joined with hyphens.
  - **Types:** `feat`, `fix`, `refactor`, `test`, `docs`, `build`, `ci`, `chore`. They match the commit types in [section 3](#3-commits).
  - **Components:** `workflow`, `qa`, `agent`, `envs`, `repo`, `skills`, `mcp`, `ui`, `frontend`, `desktop`, `packaging`, `scripts`, `docs`.
  - **Examples:** `feat/qa-confidence-ledger`, `fix/desktop-orphan-reaping`, `docs/frontend-page-template`.
  - One branch carries one component change. If a change spans two components, split it into two branches, or name the branch after the component that owns most of the code and say so in the PR.
  - Existing branches use other names: `feature/*` (for example `feature/launcher-orphans`) and `task-<id>` (agent worktrees). Do not rename them. New branches follow the convention above.
- **Release tags** are `vX.Y.Z`. **Release candidates** are `vX.Y.Z-rcN`. The `desktop-prerelease` workflow runs on `v*-rc*`. The `desktop-release` workflow runs on `v*.*.*`.
- **Merging into `develop`.** Open a pull request into `develop`. The required checks are in [section 7](#7-the-merge-gate). The orchestrator's integration flow (agent branches merged into a local integration branch, then into `develop`) is used only by the agent workflow in [AGENTS.md](../AGENTS.md).
- **Releasing.** Bump the six version fields in one `chore(release): X.Y.Z` commit on `develop`, merge `develop` into `main` keeping main's README, then tag. The tag starts the release workflow, which requires the tag commit to be on `main`. Full steps are in [RELEASING.md](RELEASING.md) and [section 9](#9-how-to-recipes).
- **Hotfixes.** Branch `hotfix/<topic>` from `main`, merge it into `main` and tag a patch version, then merge `main` back into `develop` so the fix is not lost. No `hotfix/*` branch has been used yet; this is the process to follow if one is needed.
- **Protection (paid plan).** Rulesets that require pull requests, Code Owners review and status checks; required reviewers on the `prod` environment; and secret scanning with push protection need a GitHub plan that supports them (**paid plan**). The current plan returns HTTP 403 for these settings, so they are not enforced. [RELEASING.md](RELEASING.md) has the owner checklist. Follow the rules anyway.

## 3. Commits

- **Format:** Conventional Commits, `<type>(<scope>): <imperative summary>`. The subject is short and imperative, for example `fix(desktop): reap orphaned services on Windows`.
- **Types:** `feat`, `fix`, `refactor`, `test`, `docs`, `build`, `ci`, `chore`, `style`.
- **Scope:** the component name from [section 2](#2-branching-model). Older commits use other scopes such as `ui`, `memory` and `qa`.
- **Body:** explain why the change is needed and what it guarantees, in prose, when the subject does not say it.
- **Trailer for AI-assisted work:** add a `Co-Authored-By:` line naming the model, for example `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. 183 of the last 200 commits carry one.
- **One logical change per commit.** Do not mix a refactor with a behaviour change, or code with an unrelated docs edit. Documentation for a change goes in the same commit as the change (section 5.9).
- **Release commits** are `chore(release): X.Y.Z`. Memory-log commits are `docs(memory): ...` and are made by the orchestrator only.
- Never commit `.env`, run evidence under `.maf-harness/`, or `agent-framework.pdf`.

## 4. Naming conventions

### 4.1 Python

- Modules and functions: `snake_case` (`workflow_support.py`, `case_budget()`).
- Classes: `PascalCase` (`StageExecutor`, `LocalAPI`, `ErrorCode`).
- Constants: `UPPER_CASE` (`STAGES`, `CORE_STAGES`, `SECRET_ENV_PREFIX`).
- Private helpers: a leading underscore (`_c`, `_DOTENV_KEY`, `_builtin_personas`).
- Every module starts with a docstring that says what it is for. The architecture map reads it.
- Subpackages are lowercase single words or `snake_case` files inside a package (`workflow_steps/planning.py`).

### 4.2 TypeScript and React

- Components: `PascalCase` (`DetailPage`, `ConfirmDialog`, `StatusBadge`).
- Functions and hooks: `camelCase`, with hooks named `use*` (`usePreference`, `useReturnFocus`, `api`).
- Component files in `frontend/components/` use lowercase names, with hyphens when there is more than one word (`model-settings.tsx`, `mcp-servers.tsx`, `run-shell.tsx`). Single-word names are fine (`page.tsx`, `data.tsx`). Route content lives in `components/views/`.
- Route folders follow Next.js conventions (`app/(workspace)/runs/[id]/`).
- Playwright specs: `frontend/tests/<kebab-name>.spec.ts`.
- Desktop tests sit next to the code: `desktop/src/<name>.test.ts`, run with `node:test` after compilation to `dist/`.

### 4.3 CSS

- One global stylesheet, `frontend/app/globals.css`. Design tokens are custom properties named `--*` declared on `:root`, with dark values on `:root[data-theme=dark]`. Use the tokens (`--bg`, `--surface`, `--text`, `--muted`, `--border`, `--good`, `--bad`, `--warn`) instead of literal colours.
- Classes are `kebab-case` (`.detail-dialog`, `.confirm-dialog`, `.tabs`).
- The file is grouped into blocks with comment headers (tokens, page template, tables, dialogs, run layout, sidebar links). Rules are written compactly, one rule per line. It is not a minified build artifact; edit it by hand.

### 4.4 Tests

- Python: `tests/test_<area>.py`, one file per area (`test_ui_api.py`, `test_errors.py`, `test_triage.py`).
- Test names are sentences that describe the behaviour, for example `test_skill_scripts_run_on_sample_repositories_through_python_argv`. Name the behaviour, not the method.
- Browser: `frontend/tests/<kebab>.spec.ts`. Desktop: `desktop/src/*.test.ts`.

### 4.5 Identifiers

- **Error codes:** `ENV-XX-NNN`. The families in use are `ENV-PY`, `ENV-JAVA`, `ENV-DOTNET` and `ENV-COMMON`. Codes are stable and never renumbered.
- **Plan areas:** `PLN-xx`. **Controls:** `CTL-nnn`. The host assigns these. **Requirements:** `REQ-n`. **Test cases:** `TC-n`.
- **Specs (greenfield):** `SPEC-<hash8>-<n>`.
- **Environment variables:** `HARNESS_*` for harness settings, `OPENAI_*` for the gateway, and `LLM_MODEL` as an alias of `OPENAI_MODEL`.
- **MCP secrets:** `HARNESS_MCP_SECRET_<SERVER>_<KEY>`, where the server name is upper-cased and each `-` becomes `__`. An HTTP server uses the reserved key `BEARER_TOKEN`.

Internal variables are set by the launcher, the desktop app or the code, not by users. They are not in `.env.example`.

| Variable | Set by | Purpose |
|---|---|---|
| `HARNESS_API_TOKEN` | `scripts/start_ui.py`, `desktop/src/main.ts` | Per-launch bearer token for the loopback API (at least 32 characters) |
| `HARNESS_API_PORT` | `scripts/start_ui.py`, `desktop/src/main.ts` | Loopback port of the Python API |
| `HARNESS_BUILD_ID` | `scripts/start_ui.py` | Build fingerprint; the launcher never reuses a UI started from a stale build |
| `HARNESS_RESOURCE_ROOT` | `desktop/src/main.ts` | Bundled read-only resources (`<resources>/backend` when packaged) |
| `HARNESS_DATA_DIR` | `desktop/src/main.ts` | User data folder for the packaged app |
| `HARNESS_TEST_PYTHON` | Developer, or `check_all.py` | Python used by the Playwright fixture server |

### 4.6 Files

- **Skills:** the folder name equals the `name` in `SKILL.md`.
- **Dated documents:** `docs/<TOPIC>_YYYY-MM-DD.md`. **Plans:** `docs/plans/PLAN_YYYY-MM-DD_<TOPIC>.md`. Dated reviews, plans and audit reports are historical records. Do not edit their content; a dated status note at the top is allowed.
- **Installers:** `Agentic-QE-<version>-<os>-<arch>.<ext>`, for example `Agentic-QE-1.0.7-win-x64.msi`.
- **Logs:** `.maf-harness/logs/launch-YYYYmmdd-HHMMSS-ffffff.log` for launcher logs, and `<userData>/logs/desktop-<service>-<iso>.log` for the desktop app.
- **Windows installer:** the MSI `upgradeCode` in `desktop/electron-builder.yml` is fixed for the life of the product. Never change it.

## 5. Coding standards and principles

### 5.1 Lint, format and types

- **Python:** `pyproject.toml` `[tool.ruff]`. Line length 110, target `py311`, double quotes. Rule sets: `E`, `W`, `F`, `I`, `B`, `UP`, `SIM`, `C4`, `PIE`, `RUF100`. Ignored: `E501` (the formatter wraps lines), `B008`, `SIM108`, `SIM105`, `C408`. Tests may also ignore `B011` and `SIM117`. The `skills/` folder is excluded. Run `ruff check` and `ruff format --check` on `src tests scripts`. Add type hints to new public functions.
- **Frontend:** `frontend/tsconfig.json` has `"strict": true`. Type-check with `npm run typecheck`. The frontend pins TypeScript 7.0.2 and Next.js 16.4.0 exactly, on purpose.
- **Desktop:** `desktop/tsconfig.json` has `strict` and `noUncheckedIndexedAccess`. It uses TypeScript `^5.9.0`. The two npm packages keep separate compilers and lockfiles on purpose. Do not align them without testing both.
- **Line endings and whitespace:** `.gitattributes` sets `* text=auto eol=lf`, with CRLF only for `*.cmd` and `*.bat`. `.editorconfig` sets UTF-8, LF, final newlines and 4-space Python indentation. Markdown keeps trailing whitespace.

### 5.2 Cross-OS rules

Every change must work on Windows, macOS and Linux. CI runs the Python tests on Ubuntu (3.11 and 3.12), Windows (3.12) and macOS (3.12), and the launcher smoke checks and desktop tests on all three (`ui.yml`).

- Use `pathlib` for paths. Open text files with `encoding="utf-8"`.
- Run subprocesses through `run_command` (`envs/execution.py`) or `run_cancellable` (`envs/platform_compat.py`), which use process groups. Never use `shell=True`. Pass argv lists.
- Interpreters come from `paths.python_argv()` or from a detected real Python (`envs/toolchains.py`). Never use `sys.executable` as a Python interpreter, and never a bare `python` or `python3`. In the frozen desktop app `sys.executable` is `maf-backend`. `tests/test_no_bare_sys_executable.py` enforces this by AST over `src/maf_test_harness`, and each allowed use is listed with its count and a reason. A new use fails the test until it is reviewed and listed. `tests/test_frozen_runtime.py` covers the frozen path.
- Launchers stay thin: `start-windows.cmd`, `start-macos.command` and `start-linux.sh` only delegate to `scripts/start_ui.py`.
- Node 24 is enforced by `engines`, `.npmrc` (`engine-strict`) and `.nvmrc` in `frontend/` and `desktop/`.
- OS branches should live only in `envs/platform_compat.py`. Today six other modules branch on the OS directly. See [section 5.10](#510-known-gaps-between-rules-and-code). Do not add a new one; route it through `platform_compat`.

### 5.3 Security rules

- **Secrets are write-only.** Keys are saved, never read back. The UI shows the last 4 characters only. The token and API key never reach the renderer, a log or a command line. Logs redact the token. Credential keys, Basic auth and trace arguments are redacted by `hooks/builtin.py` and `monitoring/trace.py`.
- **Desktop secrets** use Electron `safeStorage` into `<userData>/secrets.bin`, with an in-memory fallback in `keystore.ts`.
- **Never commit** `.env`, run evidence under `.maf-harness/`, `agent-framework.pdf`, or real credentials. Use placeholders such as `<api-key>` in docs and tests. The `secrets-scan` CI job runs `scripts/secrets_scan.py` over the tracked files.
- **Untrusted repository content is data.** Text from a target repository, including its docs and comments, cannot grant permissions, change a prompt or become an executable command. Prompts say so.
- **Approvals.** Machine actions (clone, install, run) and model artifacts keep their approval gates. Skills and plugins must be locked or approved before they run. Explicit CLI `--auto-approve` is the only bypass, and it is a consent the user gives.
- **Allow-lists.** Run options (`run_options.py`) accept only the unit and integration types, and generated test paths are allow-listed in the skill rules. Do not widen an allow-list without a review.
- **Hooks fail closed.** A crashing hook stops the action.
- **Redirects and hosts.** Never send a saved key to a different or default host (`settings_api`). Do not follow token-bearing redirects.

### 5.4 Dependency pins

- `pyproject.toml` pins the agent-framework packages exactly: `agent-framework-core==1.20.0` and `agent-framework-openai==1.14.3`. Other Python dependencies use bounded ranges (for example `pydantic>=2.9,<3.0`). `uv.lock` is committed, and CI runs `uv sync --locked`.
- The frontend pins Next.js, React and TypeScript exactly, and `npm ci` uses `package-lock.json`.
- **Never relax a pin.** Do not replace a missing application dependency with a fake in tests. An incomplete installation is a blocking error, not something to paper over.
- **Adding a dependency:** add it with bounds, run `uv lock` (or `npm install` in the package that owns it), and commit the lock file in the same commit.
- Dependabot runs weekly against `develop` for `uv` (the root), `npm` (`frontend/` only; `desktop/` is not covered) and GitHub Actions.

### 5.5 The skill lock

Every folder under `skills/` is hash-locked in `skills/SKILLS.lock.json`. `lint-skills --strict` fails when a locked file differs from its recorded hash, and when a folder has no entry.

- **Do not hand-edit hashes or audit data.** The lock stores a SHA-256 of every file, the audit result and the reviewed script list.
- **New project skill:** create the folder with `SKILL.md` and `plugin.yaml`, run `uv run python scripts/vendor_skills.py --lock-project`, then `uv run maf-harness lint-skills --strict`. The `--lock-project` option adds entries only for folders that have none.
- **Edited locked skill:** there is no tool in this repository that refreshes an existing entry. `vendor_skills.py` re-vendors from an upstream clone with `--source`, and `--lock-project` skips folders that already have an entry. Until a refresh tool exists, the change needs an owner decision on how to refresh the entry. Do not guess a hash. See [section 5.10](#510-known-gaps-between-rules-and-code).
- **Reviewed scripts** are listed in `REVIEWED_SCRIPTS` in `scripts/vendor_skills.py`. Adding one is a code-review decision. A reviewed script must be pure: no network, no subprocess, no writes except stdout.
- **Owner:** `/skills/` and `SKILLS.lock.json` need the owner's review (CODEOWNERS).

### 5.6 Error handling

- Every classified error has a stable code, a category, an autonomy level and a remedy in `errors/taxonomy.py`.
- **Categories:** `environment`, `infra`, `test_defect`, `app_defect`, `unknown`.
- **Autonomy:** `auto`, `auto_once`, `ask`, `manual`. `envs/recovery.py` applies at most one allow-listed action.
- **Fail-soft is deliberate and bounded.** A failed generation batch does not abort the stage, but its cases are marked `generation_failed` and block PASS. If more than `HARNESS_MAX_FAILED_BATCH_RATIO` (default 0.5) of batches fail, the stage aborts.
- **Never fake a dependency.** Incomplete installs are blocking errors.
- **Gateway errors** (401, 404, quota) are reported with a friendly message; do not retry an exhausted quota.
- **Remedies** for generated-test mistakes are in `REMEDIES` in `qa/triage.py`.

### 5.7 UI and accessibility

These rules are in `frontend/CLAUDE.md` and enforced by the Playwright specs.

- **Page template.** Build pages from `DetailPage`, `Section`, `Fields`, `Table`, `StatusBadge` and `Exports` in `components/page.tsx`. Do not build a new layout beside them.
- **One dialog for confirmations:** `ConfirmDialog`. A section's expand button opens its own page instead of a modal. Known exception: the MCP servers page still uses a native `<dialog>` (`Modal` in `mcp-servers.tsx`). Do not add another.
- **No raw JSON or Markdown in the UI.** Never render either in a `<pre>`. Offer a download (`JsonDownload`, `Exports`). `markdown.tsx` renders `SKILL.md` documents only.
- **No native `confirm` or `prompt` dialogs.** They were removed; use `ConfirmDialog`.
- **Keyboard.** Resizable panels need arrow keys and Home. Dialogs return focus (`useReturnFocus`). The page has a skip link.
- **Layout.** No sideways page scroll at 1366, 1024 or 390 px, in light and dark, and with wide fonts. `frontend/tests/visual-audit.spec.ts` checks this on every page.
- **Hydration.** Server and first client render must match. Read the URL, storage or `window` after mount (or with `useSyncExternalStore`), never in a `useState` initializer. Use `usePreference` for stored preferences.
- **Browser storage** is for per-viewer conveniences only. Wrap every access in try/catch.
- **API calls** go through `api()` in `lib/harness.ts` and `/api/harness/*`. The browser never calls `127.0.0.1:8765` directly.

### 5.8 Simplicity principles

- **Reuse before you write.** Look for a helper, type or pattern already in the code (`run_command`, `presentation.public`, the page template, `usePreference`) before adding one.
- **No speculative abstractions.** No interface with one implementation, no factory for one product, no configuration for a value that never changes.
- **Deletion over addition.** The smallest correct diff wins. Remove dead code when you find it, and say so in the PR.
- **Fix the cause once.** When a bug is reported, find every caller of the function you are changing and fix the shared function, not one caller.
- **Every change has a test.** Non-trivial logic (a branch, a loop, a parser, a money or security path) comes with a test that fails if the logic breaks. Trivial one-liners need none.
- **Mark a deliberate shortcut** with a comment that names its ceiling and the upgrade path.
- **Comments explain why,** not what.

### 5.9 Docs travel with code

In the same commit as a behaviour change, update the `CLAUDE.md` of each changed area, this guide or the relevant `docs/*.md`. If you added, removed, moved or re-wired a `.py` file under `src/`, `scripts/` or `skills/`, or a `.ts` file under `frontend/app`, `frontend/components` or `frontend/lib`, regenerate the architecture map (`uv run python scripts/gen_architecture.py`). Do not edit `docs/ARCHITECTURE_MAP.md` by hand.

### 5.10 Known gaps between rules and code

These rules are stated above. The code does not meet them yet. Fix the code, or change the rule with the owner's agreement. Do not describe the rule as enforced until it is.

- **OS branches outside `platform_compat`:** `paths.py`, `storage.py`, `qa/packaging.py`, `envs/setup_guide.py`, `repo/analyzer.py` and `envs/toolchains.py` test `sys.platform`, `os.name` or `platform.system()` directly.
- **IPC sender check:** the desktop IPC handlers in `desktop/src/main.ts` (`desktop:*`) ignore the event argument. None of them checks the sender frame's URL. The helper `isAppOrigin` in `desktop/src/util.ts` exists for this.
- **Skill lock refresh:** no command refreshes an existing lock entry after a locked file changes. See [section 5.5](#55-the-skill-lock).
- **Dialog rule:** the MCP servers page uses its own native `<dialog>` alongside `ConfirmDialog`.
- **Branch names:** the repository has `feature/*` and `task-<id>` branches that do not follow [section 2](#2-branching-model).
- **Release rules:** `docs/RELEASING.md` describes the owner checklist, which needs a paid plan for several items. Those items are not enforced.

## 6. Testing standards

### 6.1 What to test, and where

| Layer | What | Where | Command |
|---|---|---|---|
| Unit and component | pure functions, classifiers, real MAF with a scripted transport | `tests/test_<area>.py` | `uv run pytest -q --ignore=tests/targets` |
| Workflow end to end | a real `Harness` with `ScriptedStageRuntime` and `clone_fixture`, driven through every gate by `RuntimeService` | `test_harness_gates.py`, `test_execution_stage.py`, `test_fail_soft_generation.py` | the same suite |
| Static quality | lint, format, skill lock, architecture map | section 7 | `scripts/check_all.py` |
| Browser journeys | the real UI against `tests/ui_fixture_server.py` | `frontend/tests/*.spec.ts` | `npm test` in `frontend/` |
| Visual audit | layout on every page at 1366, 1024 and 390 px, light and dark, normal and wide fonts | `frontend/tests/visual-audit.spec.ts` | the same |
| Desktop shell | `node:test` for port, token, URL and keystore helpers | `desktop/src/*.test.ts` | `npm test` in `desktop/` |
| Acceptance against deployed apps | real gateway and repository; costs tokens | `tests/targets/`, skipped unless `PETSTORE_BASE_URL` is set | `uv run maf-harness run <url>` |

### 6.2 Rules

- **No network and no real model** in tests. Use `ScriptedStageRuntime` (`tests/fakes.py`), which returns schema-valid stage artifacts and replaces only the model call. Use `clone_fixture` in place of `clone_repository`.
- **Monkeypatch at the module that uses the name,** for example `monkeypatch.setattr(wf, "clone_repository", clone_fixture)`.
- **`tmp_path`** for files. Never read or write `~/.maf-harness`. `tests/conftest.py` redirects the token ledger and the hook configuration.
- **No sleeping on real time.**
- **Skip with a reason** when an OS tool is missing, so the skip is visible.
- **Keep tests OS-neutral.** Build paths with `pathlib`.
- **Fakes and scripted transport:** `fakes.py` also provides `review_response`, `design_review_response` and `local_env_plan`. To exercise a failing batch, pass `extra_cases` or raise from a scripted `run_stage`.
- **Playwright fixture server.** `tests/ui_fixture_server.py` is the real `LocalAPI` and workflow, with a scripted model and a fixture clone, and no GitHub access. Its scripted reviewers use the same derivation hook as `tests/conftest.py`: they derive assertion literals from the generated tests they review.
- **Ports.** Playwright uses the API port `PW_API_PORT` (default 8876) and the UI port `PW_UI_PORT` (default 3100). `scripts/check_all.py` picks free ports for both, so parallel worktrees do not collide.
- **Playwright needs** `PYTHONPATH=<repo>/src` so the fixture server imports the code under test, and a non-empty `OPENAI_API_KEY`. The fixture server's Python is `HARNESS_TEST_PYTHON`, or `.venv` in the repository root. `check_all.py` sets all three.
- **Split fake secrets.** Secret scanners match token-shaped strings in the source, including test files. Write a fake token as two literals joined in code, with a comment that says why. For example `"sk" + "-test-probe-secret"` in `tests/test_security_hardening.py`, and the split in `tests/test_mcp_servers.py`. Then the scan passes and the test still exercises the pattern.
- **Sandboxed shells** cannot bind ports. Tests that bind ports (the launcher, Playwright and socket tests) must run outside the sandbox.

### 6.3 Test names

Write each test name as a sentence that states the behaviour. `test_ui_api.py` and `test_triage.py` show the style. A reader should know what breaks when the test fails.

## 7. The merge gate

Run the full gate from the repository root before you open a pull request:

```bash
uv run python scripts/check_all.py
```

It runs these steps, in order, and prints a summary. Every step runs even after an earlier step fails.

| Step | What it checks | Group |
|---|---|---|
| `ruff check` | lint on `src`, `tests`, `scripts` | core |
| `ruff format --check` | formatting on the same paths | core |
| `architecture map current` | `scripts/gen_architecture.py --check` | core |
| `version files agree` | `scripts/check_version.py`: the six version fields match | core |
| `skills lint (strict)` | `maf-harness lint-skills --strict` | core |
| `pytest` | `pytest -q --ignore=tests/targets` | core |
| `frontend typecheck` | `tsc --noEmit` in `frontend/` | ui |
| `frontend build` | `next build --webpack` | ui |
| `frontend playwright` | the browser journeys and the visual audit | ui |
| `desktop shell tests` | `tsc` and `node --test` in `desktop/` | desktop |

- **Skip a group** only when that area is untouched: `--skip-ui` or `--skip-desktop`. Say which group you skipped and why in the PR.
- **Run one step** with its command: `uv run ruff check src tests scripts`, `uv run pytest -q tests/test_triage.py -k remedy`, `uv run python scripts/gen_architecture.py --check`, `uv run maf-harness lint-skills --strict`, `cd frontend && npm run typecheck`, `cd desktop && npm test`.
- **Frontend and desktop need their own installs first:** `npm ci --include=dev` in `frontend/`, and `npm ci` in `desktop/`.
- **Sandbox.** Socket and port tests fail in a restricted sandbox. Run the gate outside it.
- **Playwright** needs `npx playwright install chromium` once.

### 7.1 CI jobs

Required checks, from `.github/workflows/`:

- **`ci`**, from `ci.yml`: job `test` on Ubuntu (Python 3.11 and 3.12), Windows (3.12) and macOS (3.12). It runs lint, the architecture map and version checks on Linux, `lint-skills --strict`, the offline generation benchmark, and pytest. Job `secrets-scan` runs `scripts/secrets_scan.py`.
- **`Local UI compatibility`**, from `ui.yml`: job `local-ui` on macOS, Windows and Ubuntu. It runs the launcher smoke checks and the desktop shell tests.
- **Desktop workflows** run on tags and on manual dispatch. `desktop-prerelease` runs on `v*-rc*`, and `desktop-release` on `v*.*.*`. Both build the installers on four runners and launch each one (`scripts/desktop_smoke.py`). A release also requires the tag commit to be on `main`.
- **`nightly.yml`** runs the full suite on a schedule.

## 8. Review process

- **Author:** opens the pull request into `develop` from a branch named by [section 2](#2-branching-model), with one logical change per commit.
- **Checks:** the required CI checks in [section 7.1](#71-ci-jobs) must pass.
- **Reviewer:** `@sarveshtalele` owns every file (CODEOWNERS). The owner reviews security-sensitive changes (plugins, MCP, secrets, desktop, skills and the lock) before merge. Requiring a Code Owners review is a paid-plan setting.
- **Memory log:** a significant change (a new stage, a new skill, an architecture change, a release, a non-trivial bug fix) is logged in `memory.md` by the orchestrator. Say so in the PR. Do not edit `memory.md` yourself.

### 8.1 Review checklist

The reviewer checks each item. The author ticks the same items in the PR template.

- **Correctness:** the change does what the PR says, including the edge cases the code path reaches. Every caller of a changed function still works.
- **Security:** no secret, `.env`, run evidence or PDF committed. Untrusted input is treated as data. No `shell=True`. Redaction and approvals are unchanged or stronger. No new allow-list entries without a reason.
- **Cross-OS:** `pathlib`, UTF-8, `run_command` or `run_cancellable`, no `sys.executable` as a Python interpreter, and any OS branch goes through `envs/platform_compat.py`. For changes to file or process code, the PR says how the Windows path was checked.
- **Accessibility and UI:** the page template, `ConfirmDialog` for confirmations, no raw JSON or Markdown in the UI, keyboard support for new controls, and no new layout overflow at 390 px.
- **Tests:** the new logic has a test that fails without the change. Tests use fakes and `tmp_path`, with no network and no real model. Browser journeys are updated when a page changes.
- **Docs:** the `CLAUDE.md` of each changed area, this guide or the relevant `docs/` file, and the architecture map if modules moved.

## 9. How-to recipes

After each recipe run the checks in [section 7](#7-the-merge-gate). Commit the docs with the code.

### 9.1 Add a workflow stage

1. `workflow_support.py`: add the name to `STAGES` in order. Add it to `CORE_STAGES` only if it must finish before execution approval.
2. `agent/personas.py`: add a `PERSONAS["<stage>"]` entry (name, role, goal and rules). If the stage returns a model artifact, add a pydantic schema to `models.py`.
3. `workflow.py`: handle the stage in `Harness.enter` for host work, or let the default `self.model(...)` run it. Put the logic in a `workflow_steps/` module and add its mixin to the `Harness` bases.
4. `skills/config.py` picks skills per stage from `plugin.yaml` `stages:`. Give the stage a primary skill in `skills/`, and lock it (section 9.2).
5. UI: `ui/eta.py` `DEFAULTS` (seconds per unit), `ui/progress.py`, the label in `frontend/lib/harness.ts` if it needs special text, and `qa/templates/NN-*.md` with `qa/step_reports.py` for the step report.
6. Tests: extend a flow in `tests/test_harness_gates.py` with `ScriptedStageRuntime`, which answers by stage name. Update the diagram in `scripts/gen_architecture.py` (`FLOW`) and `ARCHITECTURE.md`, then run `uv run python scripts/gen_architecture.py`.

### 9.2 Add a skill (and lock it)

1. Create `skills/<name>/` with `SKILL.md` and `plugin.yaml`. The folder name equals the `name` in `SKILL.md`. The description is 1024 characters or fewer and says when to use the skill. The file is 500 lines or fewer, in the shape Goal, Inputs, Steps, Output, Self-check. Put detail in `references/`. Metadata goes in `plugin.yaml`; unknown keys are errors. See [SKILL_PLUGINS.md](SKILL_PLUGINS.md).
2. Lock it: `uv run python scripts/vendor_skills.py --lock-project`, then `uv run maf-harness lint-skills --strict`.
3. A companion skill needs only the folder. A new primary for a stage and kind conflicts with the existing one, and lint reports it.
4. A host script must be reviewed and listed in `REVIEWED_SCRIPTS` in `scripts/vendor_skills.py`.
5. Test it in `tests/test_skill_plugins.py` and `tests/test_skills_registry.py`.

User and third-party skills do not go here. They go in the plugin folder (`HARNESS_PLUGIN_DIR`), are unverified until approved in the UI, are companion-only, and are never host kind.

### 9.3 Add an MCP preset

1. `src/maf_test_harness/mcp_servers.py`: add an entry to `PRESETS`. Each entry has a `label`, a `note` that explains setup and any limit, a `transport` (`http` or stdio), its `url` or `command`, the `stages` it may join, and an `allowed_tools` list. Credentials are never inline; they arrive as `HARNESS_MCP_SECRET_*` or are set in the API.
2. Keep the tool list tight. A server over the per-stage budget (`MAX_MCP_TOOLS_PER_STAGE`) is skipped in that stage.
3. The preset is selected by `preset_server(preset, name)`.
4. Test it in `tests/test_mcp_servers.py`. Mark any tool name you have not verified against a live server as unverified in the `note`.
5. MCP is a security-sensitive change. The owner reviews it before merge.

### 9.4 Add an editable prompt or persona

1. **Persona** (the instructions for a stage agent or a reviewer): add an `AgentPersona` to `PERSONAS` in `agent/personas.py`. It has a `name`, a `role`, a `goal` and a tuple of `rules`. The host appends the stack expertise and the JSON contract.
2. **Reviewer:** the three confidence reviewers are `plan_reviewer`, `design_reviewer` and `test_reviewer`, listed in `REVIEWER_ROLES`. Add a new reviewer there and call it from the stage that uses it (`workflow_steps/planning.py` or `test_review.py`). Reviewer answers are judged against the host's own rubric. A prompt override that would replace a reviewer call is refused (`_targets_reviewer` in `agent/prompt_settings.py`).
3. **Editable text:** persona text is shown in the UI Prompts tab (`frontend/components/prompts.tsx`). `agent/prompt_settings.py` holds defaults, validation, loading and saving. The file is saved atomically.
4. **Prompt sections** (not personas): see section 9.9.
5. Test the change in `tests/test_prompt_assembly.py` and the prompt settings tests.

### 9.5 Add a UI page with DetailPage

Run pages live under `frontend/app/(workspace)/runs/[id]/`.

1. Add a route folder with a thin `page.tsx` that renders a view from `components/views/`.
2. Add the tab to `RUN_TABS` in `frontend/components/run-shell.tsx`.
3. Build the view from `DetailPage`, `Section`, `Fields`, `Table`, `StatusBadge` and `Exports` in `components/page.tsx`. Use `Table` for every table. Use `ConfirmDialog` for any confirmation. Offer JSON and Markdown as downloads.
4. Fetch with `api('runs/${id}/...')` from `lib/harness.ts`.
5. Add an API method in `ui/api.py` (or `EvidenceAPI` and `SettingsAPI`), and keep the response through `presentation.public`, which redacts it.
6. Add a spec in `frontend/tests/` (the routes spec mocks a rich run) and, if the Playwright journey needs it, a fixture response in `tests/ui_fixture_server.py`.
7. Check the layout at 390 px, and keep the visual audit green.

### 9.6 Add an IPC handler in the desktop shell

1. In `desktop/src/main.ts`, register the handler with `ipcMain.handle('desktop:<name>', ...)`. The channel name starts with `desktop:`.
2. Validate the sender before doing anything. Compare the sender frame's URL with the app origin:

   ```ts
   ipcMain.handle('desktop:<name>', (event, arg: string) => {
     if (!isAppOrigin(event.senderFrame?.url ?? '', uiPortNum)) throw new Error('Untrusted sender.');
     // ...
   });
   ```

   `isAppOrigin` is in `desktop/src/util.ts`, and `uiPortNum` is the UI port chosen at launch in `main.ts`. The existing handlers do not do this yet (see [section 5.10](#510-known-gaps-between-rules-and-code)). Every new handler must.
3. Expose the call in `desktop/src/preload.ts` with `contextBridge`. Pass only the arguments the renderer needs.
4. Validate the arguments in the handler. Never return a token or a saved key.
5. If you add a helper, put it in `util.ts` (no Electron imports) and test it in `util.test.ts`.
6. Run `cd desktop && npm test`.

### 9.7 Add an error code with a remedy

1. `errors/taxonomy.py`: add `_c("ENV-XX-NNN", category, autonomy, title, hint, recipes)`. Use an existing category and autonomy constant. Never renumber an existing code.
2. `errors/classifiers/<ecosystem>.py`: add `("ENV-XX-NNN", r"regex")` to `RULES` (setup errors) or `EXEC_RULES` (test-run output). Order is priority.
3. If it can be fixed automatically, add a `Recipe` to `errors/playbook.py` (`RECIPES`) and an allow-listed action in `envs/recovery.py`. `playbook.validate()` must return no problems.
4. If it is a generated-test mistake shown in the QA report, add `(re.compile(...), "advice")` to `REMEDIES` in `qa/triage.py`.
5. Add sample text to `tests/error_cases.py`, tests in `tests/test_errors.py` and `tests/test_triage.py`, and a row in [ERROR_PLAYBOOK.md](ERROR_PLAYBOOK.md).

### 9.8 Cut a release and a release candidate

Release steps are the owner's; read [RELEASING.md](RELEASING.md) first.

1. On `develop`, set the six version fields to `X.Y.Z` in one commit. The fields are `pyproject.toml` `[project] version`, the `maf-test-harness` entry in `uv.lock` (`uv lock` refreshes it), `frontend/package.json` and `frontend/package-lock.json`, and `desktop/package.json` and `desktop/package-lock.json`. For the npm files, run `npm version X.Y.Z --no-git-tag-version` in each folder.
2. Run `uv run python scripts/check_version.py`. It must pass.
3. **Release candidate:** tag `vX.Y.Z-rcN` on a commit whose six fields already say `X.Y.Z`. The `desktop-prerelease` workflow builds it and keeps the installers as workflow artifacts. It creates no release. Test it on a separate machine, because a prerelease built by dispatch carries the version in the files and its MSI replaces an installed release of the same version.
4. **Release:** merge `develop` into `main` keeping main's `README.md`, tag the `main` commit `vX.Y.Z`, and push the tag. The `desktop-release` workflow creates a draft release with installers, `SHA256SUMS` and generated notes. Check the draft, then publish it by hand.
5. Rollback on Windows needs an uninstall first, because the MSI blocks downgrades. Say so in the release notes.

### 9.9 Add a prompt section

1. Add the key to the sections dict that the stage builds (in `workflow_steps/*.py`).
2. Register it in `llm/prompts.py`: `STAGE_ORDER` (planning, design, reviews), `GENERATION_ORDER` (generation) or `GENERATION_INPUT_ORDER` (inside `approved_inputs`). Put stable keys first and per-batch keys later, so the prefix cache keeps hitting. See [CACHING.md](CACHING.md).
3. Keep the text language-neutral. Language rules belong in `LANGUAGE_RULES` (section 9.10).
4. Test it in `tests/test_prompt_assembly.py`.

### 9.10 Add a language

1. `workflow_support.py`: add a family to `LANGUAGE_RULES` (`declaration`, `parametrize`, `stdlib`, `retry`, `integration`, `rules`) and map the language name in `_FAMILY_BY_LANGUAGE`.
2. `skills/generic-test-generator/references/<lang>.md` holds the language conventions. The rest of the skill stays neutral. Refresh its lock entry, then run `lint-skills --strict`.
3. Detect the tooling in `repo/analyzer.py` (`_detect_frameworks`) and `repo/stack.py` (`detect_stack`). Write the environment spec in `envs/spec.py`, following `envs/python_env.py`, and the toolchain detection in `envs/toolchains.py`.
4. Add the ecosystem to `resolve_ecosystem` and the plan builder in `envs/runners.py`.
5. Add a classifier table in `errors/classifiers/<lang>.py`, registered in `BY_ECOSYSTEM`, `ALL_LANGUAGES` and `EXECUTION` (`errors/taxonomy.py` codes).
6. Tests: `tests/test_stack_and_runners.py`, `tests/test_errors.py`. Docs: [ENVIRONMENTS.md](ENVIRONMENTS.md).

### 9.11 Change the case budget or ETA defaults

- Cases per requirement: `HARNESS_CASES_PER_REQUIREMENT` (default 2, clamped 1 to 10). A run can override it until design is approved, through `POST /runs/<id>/case-budget`.
- Batch sizes: `HARNESS_GENERATION_BATCH_SIZE` (default 3, max 8). Failed-batch tolerance: `HARNESS_MAX_FAILED_BATCH_RATIO` (0.5). Retries: `HARNESS_GENERATION_MAX_ATTEMPTS` (3).
- ETA fallbacks: `DEFAULTS` in `ui/eta.py`. Real durations come from `audit.jsonl` and `trace.jsonl` of earlier runs.
- Tests: `tests/test_case_budget.py`, `tests/test_ui_eta.py`.

### 9.12 Package the desktop app

```bash
uv sync --extra dev --extra desktop
uv run python scripts/build_desktop.py --dry-run     # prints the steps
uv run python scripts/build_desktop.py               # PyInstaller backend, Next build, electron-builder
```

Build each OS on its own runner. `--arch x64|arm64` selects the electron-builder arch. Unsigned builds and first-run instructions are in [DESKTOP.md](DESKTOP.md). The shell itself is in [desktop/CLAUDE.md](../desktop/CLAUDE.md).

## 10. Troubleshooting for developers

### 10.1 Where evidence lives

| What | Where |
|---|---|
| Launcher logs | `.maf-harness/logs/launch-*.log` (UI: Configuration, Debugging logs). Desktop: `<userData>/logs/desktop-*.log` |
| Run folder | `.maf-harness/runs/<run-id>/`: `run.json`, `workflow.log`, `audit.jsonl`, `trace.jsonl`, `checkpoints/`, `prompts.json`, per-stage `{stage}-{revision}.json` |
| Clone and analysis | `repo/`, `reverse_engineering.json`, `clone_operation.json` |
| Generation | `generation_attempts/`, `build_checks/` |
| Install and test output | `environment/attempt-*/`, `executions/attempt-*/` (`stdout.log`, `stderr.log`, JUnit, reports) |
| Results | `qa_report.json`, `qa_report.md`, `deliverables/` (ZIP) |
| Reruns | `attempts/<id>/` (earlier evidence is kept) |
| Token ledger | per machine, `HARNESS_USAGE_DIR` ([CACHING.md](CACHING.md)) |

Tools: the UI Errors tab (grouped run errors, `ui/errors.py`), Logs, Audit, Observability, Prompts (exact prompt snapshot) and Test reports tabs. `uv run maf-harness trace <run-dir>` gives the observability report, and `verify-run` re-checks skill and MAF evidence. [OBSERVABILITY.md](OBSERVABILITY.md) covers telemetry.

Request logging is silenced on purpose in `ui/api.py` (`Handler.log_message` is a no-op), because URLs can contain sensitive repository information. For a local debugging session, call `super().log_message` in that method temporarily and do not commit the change.

### 10.2 Ports

- `start_ui.py` moves to the next free port when you did not give one, up to 10 tries. An explicit `--port` or `--api-port` is never moved.
- Exit code 10 from `--check-only` means this checkout's app is already running. Open the URL it prints, or stop it with Ctrl+C in its terminal.
- Each launcher writes its own state file, `.maf-harness/launcher-<pid>.json`, so a second instance does not overwrite the first.
- A port that is busy because of a stale UI: stop the launcher and run it again.

### 10.3 The sandbox

Sandboxed shells cannot bind local ports. The launcher tests, the Playwright tests and the socket tests fail there. Run them outside the sandbox. Live runs also need network access and a real gateway, so they run outside it too.

### 10.4 The frozen (packaged) app

- In the frozen app `sys.executable` is `maf-backend`. Use `paths.python_argv()` to run Python, and never `sys.executable`. The guard test `tests/test_no_bare_sys_executable.py` fails on a new use.
- The frozen backend reads `.env` from the user data folder, not the repository (`paths.env_file()`).
- The app writes only under user data (`HARNESS_DATA_DIR`, `app.getPath('userData')`), never under the bundled `resources/` folder. `tests/test_read_only_resources.py` checks this. Per-machine Windows installs are read-only for standard users.
- Bundled skill scripts run through `python_argv`. `tests/test_frozen_runtime.py` and `tests/test_skill_smoke_repos.py` cover this.
- Test a packaged build with `uv run python scripts/desktop_smoke.py` after building. The app must print `AGENTIC_QE_SMOKE OK` and exit 0, and leave no backend or UI process behind.

### 10.5 Windows

- Use `pathlib` and `encoding="utf-8"`. Windows venvs live in `Scripts/`, POSIX venvs in `bin/`. Do not hard-code either. Route it through `envs/platform_compat.py` (section 5.10 lists the current exceptions).
- Keep `*.cmd` files CRLF (`.gitattributes`). Everything else is LF. A checkout with `core.autocrlf` can change bytes, and the skill lock hashes exact bytes.
- Antivirus and sharing violations can lock files. The repository retries the approvals file replace. Close the app before a silent MSI upgrade. A locked file defers the change to a reboot (exit code 3010).
- Long paths and Node or `npm` problems: [WINDOWS_SETUP_AND_DEBUGGING.md](WINDOWS_SETUP_AND_DEBUGGING.md). Clone and platform diagnostics: [CLONE_AND_PLATFORM_DIAGNOSTICS.md](CLONE_AND_PLATFORM_DIAGNOSTICS.md).

### 10.6 Node and Python version errors

- `Node.js 24` is required. `engines`, `engine-strict` and `.nvmrc` refuse other majors. Install Node 24 and rerun `npm ci`.
- Python 3.12 is used by the launchers and the desktop shell. The package supports 3.11, and CI tests both.

### 10.7 Common run failures

- **401 or 404 from the gateway:** check `.env`, the base URL (include `/v1` if the gateway needs it) and the model. Restart both servers.
- **Incomplete installation:** a blocking error by design. Fix the dependency; do not fake it.
- **`generation_failed` cases:** a batch failed after retries. More than `HARNESS_MAX_FAILED_BATCH_RATIO` of batches failing aborts the stage. Failed cases block PASS.
- **Accuracy gate failure (`Accuracy gate:`):** unverified cases remain after the targeted rounds (`HARNESS_ACCURACY_ROUNDS`). Execution never starts. `HARNESS_REQUIRE_FULL_ACCURACY=0` restores the fail-soft behaviour.
- **Strict environment:** `HARNESS_STRICT_ENV` is on by default.

## Appendix: glossary

| Term | Meaning |
|---|---|
| Run | One execution of the workflow for one repository or spec set; a folder under `.maf-harness/runs/<run-id>` |
| Stage | One of the seven workflow steps in `workflow_support.STAGES` |
| Gate | A pause where a human approves, revises, skips or stops (a MAF `request_info`). Tool gates approve machine actions; review gates approve artifacts |
| Brownfield, greenfield | An existing repository, or specs from documents or Jira Cloud (`run_options.parse_run_options`) |
| Batch | Requirements or cases sent in one model call, packed by component |
| Case | One designed test. The case budget is the number of cases kept per requirement |
| Witness | An existing repository test that statically invokes a requirement's source symbol (`pre_existing_witness`). Not measured coverage |
| Requirement | A source-linked declaration candidate, or a `SPEC-<hash8>-<n>` spec, that must become a case, a witness or a reasoned gap |
| Persona | The role instructions for a stage agent or reviewer (`agent/personas.py`) |
| Skill | A folder with `SKILL.md` and `plugin.yaml`, loaded by the agent (model kind) or run by the host (host kind) |
| Plugin | A user-supplied skill in the plugin folder: companion-only, unverified until approved |
| Recipe | In `errors/playbook.py`, a recovery action id. In `repo/import_recipes.py`, a deterministic import bootstrap snippet |
| Verified, approved | A skill whose files match `SKILLS.lock.json`, or a plugin whose hash is recorded in the approvals file |
| Hook | A check run before or after a tool or model call (`hooks/`). Fails closed |
| Central and typed tools | Deterministic functions agents may call (`tools/agent_tools.py`, `tools/typed.py`) |
| Build gate | Compile and collect check of generated tests after execution approval (`tools/build_gate.py`) |
| Fail-soft | A failed generation batch does not abort the stage unless too many batches fail; its cases are `generation_failed` |
| Deliverable | ZIP of the repository with generated tests and evidence (`qa/packaging.py`) |
