# Agentic QE Platform — contributor guidance

Current branch: `new-ui`. Next.js frontend plus a loopback Python HTTP adapter over the checkpointed Microsoft Agent Framework Harness. The CLI uses the same workflow. Windows, macOS and Linux launchers use Node.js 24 and Python 3.12.

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
```

Gateway settings belong in the git-ignored `.env`. Never print or commit credentials, local run evidence or `agent-framework.pdf`. Do not delete files or rewrite user evidence without explicit authorization.

## Workflow and guardrails

Clone tool approval → repository analysis review → deterministic requirements review → planning review → component-batched design review → generated source review → Test execution setup/install approval → execution approval → scoped QA review and ZIP.

New runs use workflow version 2. `environment_setup` follows generation, and no target workspace, installation or runtime collection is created in earlier phases. Existing runs retain their saved stage order. An incomplete installation remains a blocking error; never relax repository dependency pins or replace missing application dependencies with fake tests. Runtime collection/build checks run only after execution approval. Static source checks can run during generation.

The two deterministic host skills execute reviewed scripts and record hashes, duration and output. Model stages use the assigned skills through the recorded Microsoft Agent Framework provider. Unverified repository knowledge cannot grant permissions or become executable commands. Model artifacts and machine actions retain their existing approval gates; explicit CLI auto-approval is an authorized alternative.

Retain the complete discovered declaration inventory, batch model work, and reject unexplained coverage omissions even on the final retry. Tests must exercise actual source behavior, use valid positive fixtures, preserve native framework conventions and complement existing tests. Scope generated, baseline unit and baseline integration evidence separately. Static traceability must never be labelled measured line/branch/mutation or complete application coverage.

## Module map

- `workflow.py`: checkpointed MAF WorkflowBuilder, one StageExecutor per stage, request_info gates → workflow_steps/*, agent, repo, envs, qa, hooks
- `workflow_steps/{planning,design,generation,test_review,execution,rerun}.py`: stage operations; generation = batched generate → static gates → build gate → independent review
- `workflow_support.py`: shared policy incl. LANGUAGE_RULES / language_rules() / retry_reminder()
- `agent/{runtime,personas,prompt_settings,diagnosis}.py`: create_harness_agent, SkillsProvider, middleware, per-stage models (settings.STAGE_MODELS)
- `repo/{analyzer,reverse_engineering,requirements,stack,routes,git_transport,testability}.py`: intake; analyzer has brace-aware _brace_body_end + extract_target_sources; testability.analyze_testability / retarget_cases
- `skills/{config,registry,contracts,scripts,host,reports}.py` + repo-root skills/ packages (SKILL.md + references/, SKILLS.lock.json)
- `qa/{quality,report,triage,packaging}.py`: existing_requirement_witnesses, QA report, REMEDIES/remedy_for
- `envs/` (platform_compat RUN_CANCEL/raise_if_cancelled/run_cancellable/kill_process_tree, execution.run_command, runners, toolchains), `errors/`, `hooks/` (maf_bridge raises MiddlewareFailure on stop), `tools/`, `llm/prompts.py` (section ORDER lists—register new prompt section keys there), `monitoring/`, `ui/` (loopback API, runtime_service), `frontend/` (Next.js 16 on Node 24)

Run history defaults to `<repo-root>/.maf-harness/runs`, service logs to `<repo-root>/.maf-harness/logs`; explicit overrides and old history discovery are retained. Shared toolchain/usage caches have their separate configured locations. Local execution isolation is not a security sandbox.

## Generation gate order

validate_generated_files → validate_java_traceability → _check_imports → _check_paths_are_new → collisions → _build_gate → review_generated_tests (reviewer gets authoritative target_source). Failed batches are fail-soft (generation_failures, HARNESS_MAX_FAILED_BATCH_RATIO, default 0.5); failed cases show as generation_failed and block PASS.

## Coverage accounting

Every requirement must be a new case, a pre_existing_witness (existing repo test directly invokes its source_symbol; static match, not measured coverage), or an ID-linked gap with a reason (exact ID tokens; REQ-1 ≠ REQ-10). Report states: new / pre_existing_witness / excluded / unexplained.

## Prompts and skills

Language-specific rules live only in LANGUAGE_RULES and skills/generic-test-generator/references/<lang>.md; prompts stay language-neutral. SKILL.md: name == folder, description ≤1024 chars with when-to-use, ≤500 lines, Goal→Inputs→Steps→Output→Self-check; detail in references/. After editing a locked skill file refresh only its hash in skills/SKILLS.lock.json and run lint-skills.

## Stopping a run

UI Stop (confirm) → status stopping → cancel event + task.cancel + MiddlewareFailure in MAF middleware + process-tree kill → stopped after the task ends (≤30 s). Cancel points between batches and before model calls. CLI Ctrl-C → stopped, exit 130. Resume = rerun from the active stage.

## Cross-OS rules (strict)

Every change must work on Windows, macOS and Linux; pathlib; explicit encoding="utf-8"; subprocesses via run_command/run_cancellable with process groups, never shell=True; launchers stay thin (start-windows.cmd, start-macos.command, start-linux.sh) and delegate to scripts/start_ui.py; Node 24 enforced by engines + frontend/.npmrc engine-strict + .nvmrc; CI matrix ubuntu/windows/macos.

## Orchestration and memory

Large changes are planned as tasks run by isolated worktree subagents (Sonnet for logic, Haiku for mechanical edits); only the orchestrator merges and appends a dated entry to memory.md "Core memory log".

See [strict generation and QA scope](docs/PETSTORE_STRICT_TEST_GENERATION.md), [architecture](docs/ARCHITECTURE.md), [setup](README.md), [Windows debugging](docs/WINDOWS_SETUP_AND_DEBUGGING.md) and [modular workflow](docs/MODULAR_WORKFLOW_IMPLEMENTATION.md). Dated plans and audit reports describe historical evidence; their old counts are not current release claims.
