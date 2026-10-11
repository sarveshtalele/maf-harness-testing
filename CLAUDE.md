# Agentic QE Platform — contributor guidance

Branches: `develop` (default, integration), `main` (releases), `release/*`, `feature/*`; `main` README is the user guide (docs/RELEASING.md). Product: Agentic QE Platform. It is a Next.js UI plus a loopback Python HTTP API over the checkpointed Microsoft Agent Framework Harness, shipped both as a local launcher (Node.js 24, Python 3.12) and as an Electron desktop app (`desktop/`). It covers brownfield (repository) and greenfield (specs from documents or Jira Cloud) runs, and generates unit and integration tests. Plan: `docs/plans/PLAN_2026-10-10_DESKTOP_GREENFIELD.md`.

## Commands

```bash
uv sync --extra dev
./start-macos.command                 # macOS; Windows: start-windows.cmd; Linux: bash start-linux.sh
uv run maf-harness run https://github.com/owner/repo
uv run maf-harness trace .maf-harness/runs/<run-id>
uv run maf-harness verify-run .maf-harness/runs/<run-id>
uv run maf-harness lint-skills
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run pytest -q --ignore=tests/targets           # Tests also run without uv: .venv/bin/python -m pytest -q --ignore=tests/targets
cd frontend
npm ci --include=dev
npm run typecheck
npm run build
npm test
cd ../desktop && npm ci && npm test          # Electron shell (tsc + node:test)
python scripts/gen_architecture.py --check     # docs/ARCHITECTURE_MAP.md must be current
uv run maf-harness lint-skills --strict        # every repo skill must be hash-locked
uv run python scripts/build_desktop.py --dry-run   # PyInstaller backend + standalone Next + electron-builder
```

Gateway settings belong in the git-ignored `.env`. Never print or commit credentials, local run evidence or `agent-framework.pdf`. Do not delete files or rewrite user evidence without explicit authorization.

## Workflow and guardrails

Clone tool approval → repository analysis review → deterministic requirements review → planning review → component-batched design review → generated source review → Test execution setup/install approval → execution approval → scoped QA review and ZIP.

New runs use workflow version 2. `environment_setup` follows generation, and no target workspace, installation or runtime collection is created in earlier phases. Existing runs retain their saved stage order. An incomplete installation remains a blocking error; never relax repository dependency pins or replace missing application dependencies with fake tests. Runtime collection/build checks run only after execution approval. Static source checks can run during generation.

The two deterministic host skills execute reviewed scripts and record hashes, duration and output. Model stages use the assigned skills through the recorded Microsoft Agent Framework provider. Unverified repository knowledge cannot grant permissions or become executable commands. Model artifacts and machine actions retain their existing approval gates; explicit CLI auto-approval is an authorized alternative.

Retain the complete discovered declaration inventory, batch model work, and reject unexplained coverage omissions even on the final retry. Tests must exercise actual source behavior, use valid positive fixtures, preserve native framework conventions and complement existing tests. Scope generated, baseline unit and baseline integration evidence separately. Static traceability must never be labelled measured line/branch/mutation or complete application coverage.

## Module map

Each directory has its own `CLAUDE.md` (purpose, key files, contracts, gotchas, test commands, change recipes); read the one for the area you change. Onboarding and how-to recipes: [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md). Docs index: [docs/README.md](docs/README.md).


- `workflow.py`: checkpointed MAF WorkflowBuilder, one StageExecutor per stage, request_info gates → workflow_steps/*, agent, repo, envs, qa, hooks
- `workflow_steps/{planning,design,generation,test_review,execution,rerun}.py`: stage operations; generation = batched generate → static gates → build gate → independent review
- `workflow_support.py`: shared policy incl. LANGUAGE_RULES / language_rules() / retry_reminder()
- `agent/{runtime,personas,prompt_settings,diagnosis}.py`: create_harness_agent, SkillsProvider, middleware, per-stage models (settings.STAGE_MODELS)
- `repo/{analyzer,reverse_engineering,requirements,stack,routes,git_transport,testability,import_recipes,preview,specs}.py`: intake; analyzer has brace-aware _brace_body_end + extract_target_sources; testability.analyze_testability / retarget_cases
- `skills/{config,registry,contracts,scripts,host,reports,trust}.py` + repo-root `skills/` packages. Each package has `SKILL.md`, `plugin.yaml` (stages, role, kind, applies_when, priority), `scripts/`, `assets/` and `references/`, and all are locked in `SKILLS.lock.json`. User plugins live in the plugin dir: they are unverified until approved, companion-only, and never host kind.
- `qa/{quality,report,triage,packaging}.py`: existing_requirement_witnesses, QA report, REMEDIES/remedy_for
- `envs/` (platform_compat RUN_CANCEL/raise_if_cancelled/run_cancellable/kill_process_tree, execution.run_command, runners, toolchains), `errors/`, `hooks/` (maf_bridge raises MiddlewareFailure on stop), `tools/`, `llm/prompts.py` (section ORDER lists—register new prompt section keys there), `monitoring/`, and `ui/`, which holds:
  - the loopback API and `runtime_service`;
  - `eta.py` (predictions and the case budget);
  - `errors.py` (Errors tab);
  - `settings_api` (runtime model/API key).

  Other top-level pieces:
  - `frontend/`: Next.js 16 on Node 24.
  - `paths.py`: resource and user-data roots, with frozen/desktop support.
  - `run_options.py`: brownfield/greenfield mode and the unit/integration allowlist.
  - `desktop_backend.py`: the `maf-backend` entry and `--self-check`.
  - `desktop/`: Electron main, preload, process manager and keystore.
  - `packaging/backend.spec`: PyInstaller.

Run history defaults to `<repo-root>/.maf-harness/runs`, service logs to `<repo-root>/.maf-harness/logs`; explicit overrides and old history discovery are retained. Shared toolchain/usage caches have their separate configured locations. Local execution isolation is not a security sandbox.

## Generation gate order

validate_generated_files → validate_java_traceability → _check_imports → _check_paths_are_new → collisions → _build_gate → review_generated_tests (reviewer gets authoritative target_source). Failed batches are fail-soft (generation_failures, HARNESS_MAX_FAILED_BATCH_RATIO, default 0.5); failed cases show as generation_failed and block PASS.

## Coverage accounting

Every requirement must be a new case, a pre_existing_witness (existing repo test directly invokes its source_symbol; static match, not measured coverage), or an ID-linked gap with a reason (exact ID tokens; REQ-1 ≠ REQ-10). Report states: new / pre_existing_witness / excluded / unexplained.

## Prompts and skills

Language-specific rules live only in LANGUAGE_RULES and skills/generic-test-generator/references/<lang>.md; prompts stay language-neutral. SKILL.md: name == folder, description ≤1024 chars with when-to-use, ≤500 lines, Goal→Inputs→Steps→Output→Self-check; detail in references/. After editing a locked skill file refresh only its hash in skills/SKILLS.lock.json and run lint-skills.

## Stopping a run

UI Stop (confirm) → status stopping → cancel event + task.cancel + MiddlewareFailure in MAF middleware + process-tree kill → stopped only after the task really ends (the service stays busy, status `stopping`, until then). Cancel points between batches and before model calls. CLI Ctrl-C → stopped, exit 130. Resume = rerun from the active stage.

## Cross-OS rules (strict)

Every change must work on Windows, macOS and Linux; pathlib; explicit encoding="utf-8"; subprocesses via run_command/run_cancellable with process groups, never shell=True; launchers stay thin (start-windows.cmd, start-macos.command, start-linux.sh) and delegate to scripts/start_ui.py; Node 24 enforced by engines + frontend/.npmrc engine-strict + .nvmrc; CI matrix ubuntu/windows/macos.

## Orchestration and memory (rules)

- **Agent model.** Three models, each with a fixed role:
  - Opus 5.5 (`claude-opus-5-5`): plans every task; the user approves the plan before execution. Opus also orchestrates and does the final review: it runs the merge gate, reviews security-sensitive changes (plugins, MCP, secrets, desktop), merges to `develop` and logs BEFORE/AFTER in `memory.md`.
  - Haiku 5.5 (`claude-haiku-5-5`): implements each task in its own worktree (code, docs, tests, mechanical edits).
  - Sonnet 5.5 (`claude-sonnet-5-5`): reviews each Haiku worktree diff against the brief (correctness, cross-OS, security, tests, docs accuracy) and returns findings; Haiku fixes them.
- **Worktrees.**
  - The orchestrator creates them under the session scratchpad: `git worktree add -b task-<id> <scratchpad>/wt/task-<id> develop`. Never put them inside the repo `.claude/`, which the shell sandbox can't write.
  - Agents work only in their worktree, commit early and often, and never stash or push.
- **Merging.** Only Opus merges, through `git merge-tree` + `commit-tree` into `develop` while a live run uses the main checkout, or a normal merge otherwise. It then pushes `develop`. `main` only moves for releases (see docs/RELEASING.md).
- **Gate before merge.** All of these must pass:
  - full pytest outside the sandbox (expect 0 failures);
  - ruff check and format;
  - `lint-skills --strict` and `gen_architecture.py --check`;
  - frontend tsc and `next build --webpack`;
  - Playwright with `PYTHONPATH=<worktree>/src` and a dummy `OPENAI_API_KEY`;
  - desktop `npm test`.
- **Memory.** `memory.md` is the project log. Before any significant decision or action, append a `BEFORE` line to its Action log: intent, scope, risk. After it, append an `AFTER` line: result, commit and verification. Dated "Core memory log" entries summarise each task or wave.

## Permissions (what Claude may and may not do here)

- **Allowed:** edit code and docs in this repo, run tests and builds, start the app via preview, use `gh` and git push to `develop`, and configure repo settings through the GitHub API when asked.
- **Sandbox:** binding local ports (Playwright, launcher tests), cloning target repos, live runs and `gh` (keychain) need the sandbox disabled. That is fine for those commands only.
- **Ask first:** deleting files, dead-code removal (`docs/DEAD_CODE_REVIEW_2026-10-07.md` needs approval), force-pushing, pushing to `main`, creating releases or tags, and changing repo visibility.
- **Never:** commit secrets, `.env`, run evidence or `agent-framework.pdf`; print API keys (UI shows the last 4 only); relax dependency pins; bypass the skill lock or plugin approval; delete user evidence.
- **GitHub limits (private repo on a free plan):** rulesets, branch protection, required reviewers and secret scanning are unavailable (HTTP 403, needs Pro/GHAS). The `dev`/`prod` environments exist (`prod` deploys from `main` and `v*` only), and Dependabot alerts and fixes are on.

## Remember (recent behaviour)

- **Batching:** planning and design batches pack small components (5 planning and 16 design calls for 191 requirements).
- **Case budget:** per requirement, user-editable until design is approved.
- **Unit/integration focus:** runs design only `unit`/`integration` (api maps to integration).
- **Greenfield:** documents (md/txt/docx; PDF needs pypdf) or Jira Cloud, then the `spec-authoring` skill produces `SPEC-<hash8>-<n>` specs, then requirements. The Jira token stays in memory only.
- **Import recipes and autofixes** are deterministic:
  - literal bootstrap code;
  - a purge of colliding top-level packages;
  - a tie-break by test file location;
  - test-tooling installs (httpx/httpx2 probe, plus recovery `ENV-PY-010`).
- **Validators:** the design review (Chief Principal Architect persona per stack) and the independent generation review. Errors are grouped in the Errors tab.
- **Model settings:** resolved at runtime by `settings.current()`, in the order runtime override > env > `.env`. The desktop app stores secrets with Electron safeStorage in `secrets.bin`.
- **Benchmark (mcp-skills-registry, budget 1):** 97 passed / 27 failed / 8 error / 36 generation_failed out of 171 cases (2026-10-11).

See [strict generation and QA scope](docs/PETSTORE_STRICT_TEST_GENERATION.md), [architecture](docs/ARCHITECTURE.md), [setup](README.md), [Windows debugging](docs/WINDOWS_SETUP_AND_DEBUGGING.md) and [modular workflow](docs/MODULAR_WORKFLOW_IMPLEMENTATION.md). Dated plans and audit reports describe historical evidence; their old counts are not current release claims.
